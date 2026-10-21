"""Collapse-risk grade for void-like zones: likelihood × consequence → I–IV.

Follows the project's collapse-risk review (2026-10-21). Three axes kept apart:

* Axis 0, detection confidence (high/medium/low): from classify.py. It never multiplies into the
  risk; it only changes the recommended action (low confidence → verify first).
* Axis 1, geometric hazard H (1–4) from the cover / width ratio with thresholds per material
  (Kuwano et al. for soil, Waltham thickness-span for rock).
* Axis 2, environment: count risk factors, 0 → −1, 1–2 → 0, ≥3 → +1 (Kuwano "High+"),
  surface sag +1.

L = clamp(H + modifiers, 1, 4); score = L × C; ≥12 I, 8–9 II, 4–6 III, ≤3 IV. The grade is also
computed at the shallowest cover allowed by the depth uncertainty and the worse one is used.
All thresholds are defaults for ranking what to check first; they are not calibrated
probabilities and are written into the result so the report can show them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from gpr_analyzer.detect.models import Confidence, Evidence, RiskAssessment

SOIL_RATIO_THRESHOLDS = {"sand": (0.3, 1.0, 2.0), "clay": (0.2, 0.7, 1.5)}
ROCK_RATIO_THRESHOLDS = (0.5, 1.0, 2.0)
SHALLOW_SCOPE_M = 1.5  # Japan / Seoul road-cavity criteria were built for cover up to ~1–1.5 m

GRADE_LABELS = {
    1: ("I · ฉุกเฉิน", "I · Emergency"),
    2: ("II · เร่งด่วน", "II · Urgent"),
    3: ("III · ตามแผน", "III · Planned"),
    4: ("IV · เฝ้าระวัง", "IV · Monitor"),
}

# ACTIONS[confidence][grade] = (th, en)
ACTIONS = {
    Confidence.HIGH: {
        1: ("กั้นพื้นที่และซ่อมทันที", "Close off the area and repair immediately"),
        2: ("ซ่อมภายในรอบสั้น เช่น 1–4 สัปดาห์", "Repair within a short cycle, e.g. 1–4 weeks"),
        3: ("บรรจุในแผนซ่อมประจำปี", "Include in the annual repair plan"),
        4: ("บันทึกและสำรวจซ้ำรอบถัดไป", "Record and re-survey next cycle"),
    },
    Confidence.MEDIUM: {
        1: ("กั้นพื้นที่ชั่วคราวและยืนยันด่วน (เจาะ/กล้องส่อง) ภายใน 1–2 วัน",
            "Temporarily close off and confirm urgently (drill / borescope) within 1–2 days"),
        2: ("ยืนยันภายใน 1–2 สัปดาห์ แล้วจัดเกรดใหม่", "Confirm within 1–2 weeks, then re-grade"),
        3: ("สำรวจเส้นตัดขวางหรือกริดก่อนตัดสินใจ", "Survey cross lines or a grid before deciding"),
        4: ("บันทึกและสำรวจซ้ำรอบถัดไป", "Record and re-survey next cycle"),
    },
    Confidence.LOW: {
        1: ("ยืนยันด่วนก่อน หลักฐานยังอ่อนแต่ผลกระทบสูง",
            "Verify urgently first: evidence is weak but the consequence is high"),
        2: ("สำรวจซ้ำด้วยเสาอากาศ/ทิศทางอื่นแล้วยืนยัน",
            "Re-survey with another antenna / direction, then verify"),
        3: ("สำรวจซ้ำในรอบถัดไป", "Re-survey and verify in the next cycle"),
        4: ("บันทึกไว้เท่านั้น", "Record only; verify if other evidence appears"),
    },
}


@dataclass
class RiskContext:
    """Site information the GPR data cannot give. None means "not provided"."""

    material: str | None = None  # sand | clay | rock
    groundwater: bool = False
    old_pipes: bool = False
    recent_rain: bool = False
    heavy_traffic: bool = False
    past_collapse: bool = False
    pavement: str = "normal"  # normal | cracked | sag
    consequence: int | None = None  # 1 open / 2 local road / 3 main road / 4 emergency route, building, rail
    depth_uncertainty: float = 0.2  # fraction of cover, from the velocity uncertainty
    cross_line_length_m: float | None = None

    def factors(self, material: str) -> list[tuple[str, str]]:
        out = []
        if material == "sand":
            out.append(("ทราย/วัสดุหลวม", "sand / loose fill"))
        if self.groundwater:
            out.append(("น้ำใต้ดิน", "groundwater"))
        if self.old_pipes:
            out.append(("ท่อเก่า/รั่ว", "old or leaking pipes"))
        if self.recent_rain:
            out.append(("ฝน", "rain"))
        if self.heavy_traffic:
            out.append(("รถหนัก", "heavy traffic"))
        if self.past_collapse:
            out.append(("เคยยุบ", "past collapse"))
        if self.pavement == "cracked":
            out.append(("ผิวร้าว", "cracked surface"))
        elif self.pavement == "sag":
            out.append(("ผิวทรุดเป็นแอ่ง", "surface sag"))
        return out


def grade_from_score(score: int) -> int:
    return 1 if score >= 12 else 2 if score >= 8 else 3 if score >= 4 else 4


def geometric_hazard(cover_m: float, width_m: float, material: str) -> tuple[int, float]:
    """H (1–4) and the cover/width ratio."""
    r = cover_m / max(width_m, 1e-6)
    if material == "rock":
        t = ROCK_RATIO_THRESHOLDS
        h = 4 if r < t[0] else 3 if r < t[1] else 2 if r < t[2] else 1
    else:
        t = SOIL_RATIO_THRESHOLDS["sand" if material == "sand" else "clay"]
        h = 4 if r <= t[0] else 3 if r <= t[1] else 2 if r <= t[2] else 1
        if cover_m <= 0.3:
            h = 4
        if width_m >= 1.5:
            h = max(h, 3)
    return h, r


def _likelihood(h: int, modifier: int, pavement: str) -> int:
    return max(1, min(4, h + modifier + (1 if pavement == "sag" else 0)))


def velocity_depth_uncertainty(velocity: float, spread: float | None, default: float = 0.2) -> float:
    """Fractional depth uncertainty from a velocity estimate (depth scales linearly with v)."""
    if not spread or velocity <= 0:
        return default
    return max(0.05, min(0.5, spread / velocity))


def assess_collapse_risk(cover_m: float, width_m: float | None, confidence: Confidence,
                         context: RiskContext | None = None) -> RiskAssessment:
    ctx = context or RiskContext()
    reasons: list[Evidence] = []
    material = ctx.material or "sand"
    if ctx.material is None:
        reasons.append(Evidence("warning", "material_assumed",
                                "ไม่ได้ระบุชนิดวัสดุ ใช้เกณฑ์ทราย (ระมัดระวังที่สุด)",
                                "Material not given; using sand thresholds (most conservative)"))
    consequence = ctx.consequence or 3
    if ctx.consequence is None:
        reasons.append(Evidence("warning", "consequence_assumed",
                                "ไม่ได้ระบุสิ่งที่อยู่ด้านบน ใช้ผลกระทบระดับ 3 (ถนนสายหลัก)",
                                "Surface use not given; assuming consequence 3 (main road)"))

    factors = ctx.factors(material)
    n = len(factors)
    modifier = -1 if n == 0 else 1 if n >= 3 else 0
    cover_worst = max(0.0, cover_m * (1 - ctx.depth_uncertainty))
    params = {"context": asdict(ctx), "material_used": material, "consequence_used": consequence,
              "soil_ratio_thresholds": SOIL_RATIO_THRESHOLDS, "rock_ratio_thresholds": ROCK_RATIO_THRESHOLDS,
              "grade_bands": {"I": ">=12", "II": "8-9", "III": "4-6", "IV": "<=3"}}

    if ctx.cross_line_length_m is None:
        reasons.append(Evidence("warning", "no_cross_line",
                                "ไม่มีเส้นตัดขวาง พื้นที่และรูปร่างโพรงยังไม่รู้",
                                "No cross line: cavity area and shape are unknown"))

    if not width_m:
        reasons.append(Evidence("warning", "no_width",
                                "ไม่รู้ระยะห่าง trace จึงคำนวณความกว้างและเกรดไม่ได้",
                                "Trace spacing unknown, so width and grade cannot be computed"))
        th, en = ACTIONS[confidence][3]
        return RiskAssessment(None, "ไม่มีเกรด", "Not graded", None, modifier, n, None, consequence, None,
                              None, cover_m, cover_worst, None, None, material, confidence, th, en,
                              reasons, params)

    h_nom, r_nom = geometric_hazard(cover_m, width_m, material)
    h_w, r_w = geometric_hazard(cover_worst, width_m, material)
    l_nom = _likelihood(h_nom, modifier, ctx.pavement)
    l_w = _likelihood(h_w, modifier, ctx.pavement)
    g_nom = grade_from_score(l_nom * consequence)
    g_w = grade_from_score(l_w * consequence)
    use_worst = g_w < g_nom
    h, r, lk, g = (h_w, r_w, l_w, g_w) if use_worst else (h_nom, r_nom, l_nom, g_nom)

    crit = {"rock": "เกณฑ์หิน", "sand": "เกณฑ์ทราย: ≤0.3 วิกฤต", "clay": "เกณฑ์ดินแน่น"}[material]
    reasons.insert(0, Evidence(
        "found" if h >= 3 else "absent", "geometric_hazard",
        f"ความรุนแรงเรขาคณิต H{h}: cover/กว้าง = {r:.2f} ({crit})",
        f"Geometric hazard H{h}: cover/width = {r:.2f} ({material} thresholds)"))
    sag = " และแอ่งทรุด +1" if ctx.pavement == "sag" else ""
    reasons.insert(1, Evidence(
        "found" if n >= 3 else "absent", "environment_factors",
        f"ปัจจัยแวดล้อม {n} ข้อ" + (": " + ", ".join(f[0] for f in factors) if n else "")
        + f" → ปรับ {modifier:+d}{sag}",
        f"{n} environment factor(s)" + (": " + ", ".join(f[1] for f in factors) if n else "")
        + f" → {modifier:+d}" + (" and surface sag +1" if sag else "")))
    if use_worst:
        reasons.append(Evidence(
            "warning", "worst_case_cover",
            f"ที่ cover ต่ำสุด {cover_worst:.2f} ม. เกรดแย่ลงจาก {GRADE_LABELS[g_nom][0]} จึงใช้เกรดที่แย่กว่า",
            f"At the shallowest cover {cover_worst:.2f} m the grade worsens from "
            f"{GRADE_LABELS[g_nom][1]}; using the worse grade"))
    if confidence == Confidence.LOW:
        reasons.append(Evidence("warning", "low_confidence",
                                "ความมั่นใจว่าเป็นโพรงต่ำ การดำเนินการแรกคือยืนยัน",
                                "Low confidence that this is a cavity: verify first"))
    if material == "rock" and width_m > cover_m:
        reasons.append(Evidence("warning", "waltham_span",
                                "โพรงกว้างกว่าความหนาหลังคาหิน (เงื่อนไขภัยตาม Waltham)",
                                "Cavity is wider than the rock roof is thick (Waltham hazard condition)"))
    if material != "rock" and cover_worst > SHALLOW_SCOPE_M:
        reasons.append(Evidence("warning", "shallow_criteria_out_of_scope",
                                "cover ลึกกว่า ~1.5 ม. เกณฑ์โพรงใต้ถนน (ญี่ปุ่น/โซล) ไม่ครอบคลุม ควรใช้ข้อมูลธรณีประกอบ",
                                "Cover deeper than ~1.5 m: road-cavity criteria (Japan/Seoul) do not apply; "
                                "use geotechnical data"))

    th, en = ACTIONS[confidence][g]
    return RiskAssessment(
        grade=g, grade_label_th=GRADE_LABELS[g][0], grade_label_en=GRADE_LABELS[g][1], hazard=h,
        environment_modifier=modifier, n_environment_factors=n, likelihood=lk, consequence=consequence,
        score=lk * consequence, grade_nominal=g_nom, cover_m=cover_m, cover_worst_m=round(cover_worst, 4),
        width_m=width_m, cover_width_ratio=round(r, 4), material=material, confidence=confidence,
        action_th=th, action_en=en, reasons=reasons, parameters=params)
