"""Rule-based classification of zones: suspected type, high / medium / low, ✓ ✗ ⚠ reasons.

Each type has a short list of signal features (handoff table). A level is given from which
features are present; there is no percentage because nothing here has been calibrated against
ground truth yet. "Void-like" in particular needs at least one cavity-specific sign (roof
polarity reversed against the surface, edge diffractions, ringing inside, pull-up below):
a strong, bounded reflection alone — red in an energy view — is never more than low.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import label
from scipy.signal import find_peaks

from gpr_analyzer.detect.models import (
    ArtifactFlag,
    Confidence,
    Evidence,
    Hyperbola,
    TypeAssessment,
    Zone,
    ZoneType,
)

PRIORITY = {ZoneType.ARTIFACT: 5, ZoneType.POINT: 4, ZoneType.LAYER: 3, ZoneType.VOID_LIKE: 2,
            ZoneType.SCATTERED: 1}


@dataclass
class ClassifyParams:
    contrast_threshold: float = 4.0
    strong_factor: float = 2.0  # peak contrast ≥ factor × threshold → "strong reflection"
    min_blob_px: int = 6
    polarity_agreement: float = 0.7


@dataclass
class Context:
    data: np.ndarray  # input section (samples, traces)
    contrast: np.ndarray
    period: int
    surface_sign: int  # +1 / −1, 0 unknown
    hyperbolas: list[Hyperbola]
    artifacts: list[ArtifactFlag]
    dt_ns: float
    limbs: np.ndarray | None = None  # pixels on the limbs of fitted hyperbolas, beyond their apex cores


def E(status: str, code: str, th: str, en: str) -> Evidence:
    return Evidence(status, code, th, en)


# --------------------------------------------------------------------------- features

def zone_features(z: Zone, ctx: Context, p: ClassifyParams) -> dict[str, Any]:
    c, per, d = ctx.contrast, ctx.period, ctx.data
    n_s, n_t = c.shape
    s0, s1, x0, x1 = z.sample_start, z.sample_end, z.trace_start, z.trace_end
    box = c[s0:s1 + 1, x0:x1 + 1]
    raw = box >= p.contrast_threshold
    f: dict[str, Any] = {"peak_contrast": float(box.max()), "n_traces": z.n_traces, "n_samples": s1 - s0 + 1}
    f["strong"] = f["peak_contrast"] >= p.strong_factor * p.contrast_threshold
    f["fill"] = float(raw.mean())
    lab, n = label(raw)
    sizes = np.bincount(lab.ravel())[1:] if n else np.array([])
    f["n_blobs"] = int(np.sum(sizes >= p.min_blob_px))
    # the same, leaving out pixels on limbs of hyperbolas already found: crossing limbs of stones or
    # pipes are not scattering of their own
    f["n_blobs_on_limbs"] = 0
    if ctx.limbs is not None and n:
        own = raw & ~ctx.limbs[s0:s1 + 1, x0:x1 + 1]
        lab2, n2 = label(own)
        sizes2 = np.bincount(lab2.ravel())[1:] if n2 else np.array([])
        f["n_blobs_on_limbs"] = max(0, f["n_blobs"] - int(np.sum(sizes2 >= p.min_blob_px)))

    # roof: first above-threshold sample on each trace
    has = raw.any(axis=0)
    roof = np.where(has, raw.argmax(axis=0), -1)
    xs = np.flatnonzero(has)
    f["roof_coverage"] = float(has.mean())
    if xs.size >= 3:
        fit = np.polyfit(xs, roof[xs], 1)
        resid = roof[xs] - np.polyval(fit, xs)
        f["roof_resid_periods"] = float(np.std(resid) / per)
        f["roof_slope_samples_per_trace"] = float(fit[0])
        med = np.median(roof[xs])
        core = xs[np.abs(roof[xs] - med) <= per]
    else:
        f["roof_resid_periods"] = float("inf")
        f["roof_slope_samples_per_trace"] = 0.0
        core = xs
    f["long_reflector"] = bool(z.features.get("long_reflector"))
    # a traced long reflector is continuous by construction; its box may also hold crossing events
    f["continuous"] = f["long_reflector"] or (f["roof_coverage"] >= 0.8 and f["roof_resid_periods"] <= 1.0)
    f["thin"] = f["n_samples"] <= 3 * per
    f["wide"] = z.n_traces >= max(30, n_t // 5)
    f["narrow_tall"] = z.n_traces <= 5 and (s1 - s0) >= 0.3 * n_s
    f["bounded"] = x0 > 2 and x1 < n_t - 3 and z.n_traces < 0.6 * n_t
    f["full_line_flat"] = (z.n_traces >= 0.9 * n_t and f["continuous"]
                           and abs(f["roof_slope_samples_per_trace"]) * n_t <= per)
    core_runs = _longest_run(core)
    f["roof_core"] = [int(x0 + core_runs[0]), int(x0 + core_runs[1])] if core.size else None

    # polarity of the roof against the surface (direct wave)
    f["roof_sign"], f["roof_sign_agreement"] = _roof_sign(d, c, s0, x0, roof, core, per, n_s)

    # hyperbolas related to the zone
    t_roof = (s0 + (np.median(roof[core]) if core.size else 0)) * ctx.dt_ns
    inside = [h for h in ctx.hyperbolas if x0 <= h.apex_trace <= x1
              and z.time_start_ns - per * ctx.dt_ns <= h.apex_time_ns <= z.time_end_ns]
    f["apexes"] = [(h.apex_trace, round(h.apex_time_ns, 2)) for h in inside]
    centre, half = (x0 + x1) / 2, z.n_traces / 2
    central = [h for h in inside if abs(h.apex_trace - centre) <= 0.25 * half * 2 and not h.one_sided]
    f["central_apex"] = len(central) >= 1
    f["n_apex"] = len(inside)
    f["n_apex_two_sided"] = sum(not h.one_sided for h in inside)
    roof_level = [h for h in inside if abs(h.apex_time_ns - t_roof) <= 2 * per * ctx.dt_ns]
    left = [h for h in roof_level if h.apex_trace < centre - 0.15 * half]
    right = [h for h in roof_level if h.apex_trace > centre + 0.15 * half]
    f["edge_hyperbolas"] = bool(left and right) and not (len(inside) == 1)
    f["one_edge_hyperbola"] = bool(left or right) and not f["edge_hyperbolas"]
    hw = max((h.half_width_traces for h in central), default=0)
    f["compact"] = f["central_apex"] and z.n_traces <= 2 * hw + 2 * per + 10

    # ringing / multiples inside: regularly spaced envelope peaks under the roof core
    f["n_multiples"], f["multiples"] = _multiples(c, s0, s1, x0, core, per, p.contrast_threshold)
    # pull-up of a deeper reflector beneath the zone
    f["pull_up"] = _pull_up(c, z, core, per, p.contrast_threshold)

    # artifacts
    f["stripes_near"] = [a.id for a in ctx.artifacts if a.id in z.nearby_artifacts and a.kind != "bottom_noise"]
    noise = [a for a in ctx.artifacts if a.kind == "bottom_noise"]
    f["in_bottom_noise"] = 0.0
    if noise:
        on = noise[0].time_start_ns
        f["in_bottom_noise"] = float(np.clip((z.time_end_ns - on) / max(z.time_end_ns - z.time_start_ns, 1e-9), 0, 1))
    ring = [a for a in ctx.artifacts if a.kind == "ringing"]
    span = max(z.time_end_ns - z.time_start_ns, ctx.dt_ns)
    f["in_ringing"] = float(max((max(0.0, min(a.time_end_ns, z.time_end_ns) - max(a.time_start_ns, z.time_start_ns))
                                 for a in ring), default=0.0) / span)
    stat = [a for a in ctx.artifacts if a.kind == "stationary_traces"]
    f["stationary_overlap"] = float(sum(max(0, min(a.trace_end, x1) - max(a.trace_start, x0) + 1) for a in stat)
                                    / z.n_traces)
    return f


def _longest_run(idx: np.ndarray) -> tuple[int, int]:
    if idx.size == 0:
        return (0, -1)
    best, start, prev = (int(idx[0]), int(idx[0])), int(idx[0]), int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > 2:
            start = i
        prev = i
        if prev - start > best[1] - best[0]:
            best = (start, prev)
    return best


def _roof_sign(d, c, s0, x0, roof, core, per, n_s):
    signs, weights = [], []
    for xi in core:
        a = s0 + int(roof[xi])
        b = min(n_s, a + 2 * per)
        x = x0 + int(xi)
        k = a + int(np.argmax(c[a:b, x]))
        lo, hi = max(0, k - max(1, per // 4)), min(n_s, k + max(1, per // 4) + 1)
        seg = d[lo:hi, x]
        j = int(np.argmax(np.abs(seg)))
        signs.append(np.sign(seg[j]))
        weights.append(abs(seg[j]))
    if not signs:
        return 0, 0.0
    signs, weights = np.array(signs), np.array(weights)
    pos = weights[signs > 0].sum()
    tot = weights.sum()
    if tot == 0:
        return 0, 0.0
    sign = 1 if pos >= tot / 2 else -1
    return sign, float(max(pos, tot - pos) / tot)


def _multiples(c, s0, s1, x0, core, per, thr):
    if core.size < 3:
        return 0, False
    prof = c[s0:s1 + 1, x0 + core].mean(axis=1)
    peaks, _ = find_peaks(prof, height=thr, distance=max(2, per))
    if peaks.size < 3:
        return int(peaks.size), False
    gaps = np.diff(peaks)
    regular = np.std(gaps) <= 0.3 * np.mean(gaps)
    return int(peaks.size), bool(regular)


def _pull_up(c, z: Zone, core, per, thr) -> str:
    """'found' / 'absent' / 'not_assessed' (no reflector below to judge by)."""
    n_s, n_t = c.shape
    if core.size < 3:
        return "not_assessed"
    a = z.sample_end + per
    b = min(n_s, z.sample_end + max(20 * per, (z.sample_end - z.sample_start) * 3))
    if b - a < 2 * per:
        return "not_assessed"
    w = max(10, z.n_traces // 2)
    band = c[a:b]
    pick = a + band.argmax(axis=0)
    ok = band.max(axis=0) >= thr
    ins = z.trace_start + core
    left = np.arange(max(0, z.trace_start - w), z.trace_start)
    right = np.arange(z.trace_end + 1, min(n_t, z.trace_end + 1 + w))
    left, right = left[ok[left]], right[ok[right]]
    ins = ins[ok[ins]]
    if left.size < 5 or right.size < 5 or ins.size < 0.5 * core.size:
        return "not_assessed"
    ml, mr, mi = np.median(pick[left]), np.median(pick[right]), np.median(pick[ins])
    if abs(ml - mr) > 1.5 * per:
        return "not_assessed"  # the deep reflector dips; a shift inside would be ambiguous
    return "found" if mi < min(ml, mr) - 0.75 * per else "absent"


# --------------------------------------------------------------------------- rules

def _yn(ok: bool) -> str:
    return "found" if ok else "absent"


def assess_point(f) -> TypeAssessment | None:
    ev = [E(_yn(f["n_apex"] > 0), "hyperbola_apex",
            f"hyperbola ชัด ยอดอยู่ที่ trace {f['apexes'][0][0]}" if f["apexes"] else "ไม่พบ hyperbola ในโซน",
            f"clear hyperbola, apex at trace {f['apexes'][0][0]}" if f["apexes"] else "no hyperbola in the zone"),
          E(_yn(f["central_apex"] and f["n_apex"] == 1), "single_apex",
            "ยอดเดียวกลางโซน", "single apex at the zone centre"),
          E(_yn(f["compact"]), "compact", "โซนกว้างเท่าขา hyperbola ไม่มีแนวสะท้อนอื่นต่อ",
            "zone is no wider than the hyperbola limbs")]
    if f["n_apex"] == 0:
        return None
    if f["central_apex"] and f["n_apex"] == 1 and f["compact"]:
        lvl = Confidence.HIGH
    elif f["central_apex"]:
        lvl = Confidence.MEDIUM
    else:
        lvl = Confidence.LOW
    return TypeAssessment(ZoneType.POINT, lvl, ev)


def assess_layer(f) -> TypeAssessment | None:
    ev = [E(_yn(f["continuous"]), "continuous", "แนวสะท้อนต่อเนื่องหลาย trace", "reflection continuous over many traces"),
          E(_yn(f["wide"]), "wide", f"ยาว {f['n_traces']} traces", f"{f['n_traces']} traces long"),
          E(_yn(f["thin"]), "thin", "บาง (ไม่เกิน ~3 คาบคลื่น)", "thin (≤ ~3 wavelet periods)"),
          E("absent" if f["n_apex_two_sided"] else "found", "no_apex", "ไม่มียอด hyperbola ในแนว",
            "no hyperbola apex along it")]
    if f["full_line_flat"]:
        ev.append(E("warning", "possible_ringing", "แนวราบเวลาเดียวกันตลอดเส้น อาจเป็น ringing ของเสาอากาศ ไม่ใช่ชั้นจริง",
                    "flat at one time over the whole line: may be antenna ringing rather than a layer"))
    if not f["continuous"]:
        return None
    if f["wide"] and f["thin"]:
        lvl = Confidence.HIGH if not f["n_apex_two_sided"] else Confidence.MEDIUM
    elif f["thin"] or (f["wide"] and f["long_reflector"]):
        lvl = Confidence.MEDIUM  # a wide but thick box (e.g. a roof with ringing below) is not one reflector
    else:
        lvl = Confidence.LOW
    if f["full_line_flat"] and lvl == Confidence.HIGH:
        lvl = Confidence.MEDIUM
    return TypeAssessment(ZoneType.LAYER, lvl, ev)


def assess_void(f, surface_sign: int, p: ClassifyParams) -> TypeAssessment | None:
    ev = [E(_yn(f["strong"]), "strong_reflection", "สะท้อนแรง", "strong reflection"),
          E(_yn(f["bounded"]), "bounded", "ไม่ต่อเนื่อง มีขอบเขตด้านข้าง", "laterally bounded, not continuous")]
    if surface_sign == 0 or f["roof_sign"] == 0 or f["roof_sign_agreement"] < p.polarity_agreement:
        ev.append(E("not_assessed", "polarity_reversal",
                    "ตรวจขั้วสัญญาณที่หลังคาไม่ได้ (ไม่มีคลื่นตรงอ้างอิงหรือขั้วไม่สม่ำเสมอ)",
                    "roof polarity not assessable (no direct-wave reference or inconsistent sign)"))
        reversal = False
    else:
        reversal = f["roof_sign"] == -surface_sign
        ev.append(E(_yn(reversal), "polarity_reversal",
                    "ขั้วสัญญาณที่หลังคากลับเทียบผิวดิน" if reversal else "ไม่พบขั้วสัญญาณกลับที่หลังคา",
                    "roof polarity reversed against the surface" if reversal else "no polarity reversal at the roof"))
    ev.append(E(_yn(f["edge_hyperbolas"]), "edge_hyperbolas",
                "hyperbola ที่ขอบทั้งสองด้าน" if f["edge_hyperbolas"] else
                ("hyperbola ที่ขอบด้านเดียว" if f["one_edge_hyperbola"] else "ไม่พบ hyperbola ที่ขอบ"),
                "diffraction hyperbolas at both edges" if f["edge_hyperbolas"] else
                ("diffraction at one edge only" if f["one_edge_hyperbola"] else "no edge diffractions")))
    ev.append(E(_yn(f["multiples"]), "multiples",
                f"multiples ด้านใน {f['n_multiples']} ชั้นระยะเท่ากัน" if f["multiples"] else "ไม่พบ multiples ด้านใน",
                f"{f['n_multiples']} evenly spaced multiples inside" if f["multiples"] else "no multiples inside"))
    pu = f["pull_up"]
    ev.append(E(pu, "pull_up",
                {"found": "ชั้นใต้โซนยกขึ้น (pull-up)", "absent": "ชั้นใต้โซนไม่ยกขึ้น",
                 "not_assessed": "ไม่มีชั้นใต้โซนให้ตรวจ pull-up"}[pu],
                {"found": "deeper reflector pulled up beneath", "absent": "no pull-up of the deeper reflector",
                 "not_assessed": "no deeper reflector to check pull-up"}[pu]))
    specific = sum([reversal, f["edge_hyperbolas"], f["multiples"], pu == "found"])
    # pull-up alone is not enough for medium: limbs of a pipe or stone crossing a deeper layer look the same
    core_specific = specific - (pu == "found")
    if not (f["strong"] or specific):
        return None
    if reversal and specific >= 3:
        lvl = Confidence.HIGH
    elif core_specific >= 1 and (specific + f["strong"] + f["bounded"]) >= 3:
        lvl = Confidence.MEDIUM
    else:
        lvl = Confidence.LOW
    if f["compact"] and lvl == Confidence.HIGH:  # one central hyperbola looks more like a pipe
        lvl = Confidence.MEDIUM
    return TypeAssessment(ZoneType.VOID_LIKE, lvl, ev)


def assess_scattered(f) -> TypeAssessment | None:
    own = f["n_blobs"] - f.get("n_blobs_on_limbs", 0)  # blobs not explained by diffractions from elsewhere
    ev = [E(_yn(own >= 4), "many_reflectors", f"สะท้อนแรงไม่เป็นระเบียบ {own} จุด",
            f"{own} irregular strong reflections"),
          E(_yn(f["fill"] <= 0.35), "patchy", "กระจายเป็นหย่อม", "patchy"),
          E(_yn(not f["continuous"]), "not_continuous", "ไม่ต่อเนื่องเป็นแนว", "no continuous reflector")]
    if f.get("n_blobs_on_limbs"):
        ev.append(E("warning", "blobs_on_limbs",
                    f"{f['n_blobs_on_limbs']} จุดอยู่บนขา hyperbola ของวัตถุจุดที่พบแล้ว ไม่นับเป็นการกระจัดกระจาย",
                    f"{f['n_blobs_on_limbs']} spots lie on limbs of hyperbolas already found; "
                    "not counted as scattering"))
    if f["n_blobs"] < 3 or f["continuous"]:
        return None
    if own >= 6 and f["fill"] <= 0.35:
        lvl = Confidence.HIGH
    elif own >= 4:
        lvl = Confidence.MEDIUM
    else:
        lvl = Confidence.LOW
    return TypeAssessment(ZoneType.SCATTERED, lvl, ev)


def assess_artifact(f) -> TypeAssessment | None:
    ev = []
    lvl = None
    if f["in_bottom_noise"] >= 0.5:
        ev.append(E("found", "in_bottom_noise", "อยู่ในช่วง noise ก้นภาพที่ถูก gain ขยาย",
                    "inside the gain-amplified bottom noise"))
        lvl = Confidence.HIGH
    if f["narrow_tall"]:
        ev.append(E("found", "narrow_tall", "แคบไม่กี่ trace แต่สูงเกือบทั้งความลึก ลักษณะแถบแนวตั้ง",
                    "only a few traces wide but spans much of the depth: a vertical band"))
        lvl = lvl or Confidence.MEDIUM
    if f["in_ringing"] >= 0.5 and not f["n_apex"]:
        ev.append(E("found", "in_ringing", "อยู่ในแถบ ringing แนวนอน (จุดสว่างเล็ก ๆ เรียงตลอดเส้น)",
                    "inside a horizontal ringing band (rows of small specks along the line)"))
        lvl = lvl or Confidence.MEDIUM
    if f["stationary_overlap"] >= 0.5:
        ev.append(E("found", "stationary", "อยู่บน trace ที่หยุดเดิน", "on stationary (stopped) traces"))
        lvl = lvl or Confidence.MEDIUM
    return TypeAssessment(ZoneType.ARTIFACT, lvl, ev) if lvl else None


def artifact_warnings(z: Zone, ctx: Context) -> list[Evidence]:
    out = []
    for a in ctx.artifacts:
        if a.id not in z.nearby_artifacts:
            continue
        if a.kind == "ringing":
            out.append(E("warning", "ringing_near",
                         f"โซนทับแถบ ringing แนวนอน {a.time_start_ns:.1f}–{a.time_end_ns:.1f} ns ({a.id})",
                         f"zone overlaps the horizontal ringing band {a.time_start_ns:.1f}–{a.time_end_ns:.1f} ns ({a.id})"))
        elif a.kind == "bottom_noise":
            out.append(E("warning", "bottom_noise_near",
                         f"ส่วนล่างของโซนถึงช่วง noise ก้นภาพ ({a.id}, ตั้งแต่ {a.time_start_ns:.0f} ns)",
                         f"zone reaches the bottom noise ({a.id}, from {a.time_start_ns:.0f} ns)"))
        else:
            label_th = {"vertical_stripe": "แถบแนวตั้ง", "dead_trace": "trace ไม่มีสัญญาณ",
                        "clipped_trace": "trace อิ่มตัว", "stationary_traces": "trace หยุดเดิน"}[a.kind]
            out.append(E("warning", f"near_{a.kind}",
                         f"{label_th} trace {a.trace_start}–{a.trace_end} น่าจะเป็น artifact ({a.id})",
                         f"{a.kind.replace('_', ' ')} at traces {a.trace_start}–{a.trace_end}, "
                         f"likely an artifact ({a.id})"))
    return out


def classify_zone(z: Zone, ctx: Context, params: ClassifyParams | None = None) -> Zone:
    p = params or ClassifyParams()
    f = zone_features(z, ctx, p)
    z.features = {k: v for k, v in f.items() if not isinstance(v, float) or np.isfinite(v)}
    cands = [a for a in (assess_artifact(f), assess_point(f), assess_layer(f),
                         assess_void(f, ctx.surface_sign, p), assess_scattered(f)) if a]
    warn = artifact_warnings(z, ctx)
    if not cands:
        z.suspected_type, z.confidence = ZoneType.UNCLASSIFIED, Confidence.LOW
        z.evidence = [E("absent", "no_pattern", "ไม่เข้าลักษณะประเภทใดชัด", "matches no type clearly")] + warn
        z.alternatives = []
        return z
    # same level: artifact first, then the type with more of its own signs found, then the more conservative type
    # an artifact verdict wins at its level: a pattern on stopped traces or in gained noise is not trusted
    cands.sort(key=lambda a: (a.confidence.rank, a.type == ZoneType.ARTIFACT, sum(e.status == "found" for e in a.evidence),
                              PRIORITY[a.type]), reverse=True)
    best = cands[0]
    z.suspected_type, z.confidence = best.type, best.confidence
    z.evidence = best.evidence + warn
    z.alternatives = cands[1:]
    return z
