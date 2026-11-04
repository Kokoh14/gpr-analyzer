"""Review queue for the confirm screen: every place in a job that needs a person's decision, in one list.

Three kinds of item:
- zone: an automatic zone from detect/ (void-like, pipe/point, scattered, layer), reviewed with Job.review;
- screen: a stretch of line found by a target screen that has no zone type yet. Today one screen:
  "moisture" = the signal dies much faster with depth than along the rest of the line (wet or clayey
  ground, fouled ballast). Relative to the same line, so it works without calibration on IDS and ImpulseRadar;
- audit: a random stretch where nothing was found (10 % of empty windows, at most AUDIT_MAX per line), so the
  reviewer also checks what the program calls normal.

Screen and audit decisions are kept in Job.screen_reviews and labels.jsonl. They are not zones and do not
change the report; a reviewer who sees a real target draws a zone, which goes through the normal review.
"""

from __future__ import annotations

import math
import random
import zlib
from typing import TYPE_CHECKING, Any

import numpy as np

from gpr_analyzer.report.rules import suggest_action

if TYPE_CHECKING:  # pragma: no cover
    from gpr_analyzer.app.jobs import Job, LineState

# zone type -> target of the filter chips on the confirm screen
TARGET_OF_TYPE = {"void_like": "void", "pipe_point": "pipe", "scattered": "loose", "layer": "layer",
                  "unknown": "other"}
TARGETS = ("void", "moisture", "pipe", "loose", "layer", "other", "audit")
DECISIONS = ("confirm", "normal", "unusable", "reset")
ACTION_RANK = {"E": 0, "1": 1, "2": 2, "3": 3, "none": 4}
CONF_RANK = {"high": 0, "medium": 1, "low": 2, "confirmed": 0}

# moisture screen
SHALLOW_M = (0.15, 0.6)  # metres below the surface
DEEP_M = (0.6, 1.8)
MIN_DROP_DB = 3.0  # at least this much weaker deep/shallow ratio than the line's median
MIN_LEN_M = 3.0
MAX_SCREEN_PER_LINE = 30
# audit
AUDIT_WINDOW_M = 10.0
AUDIT_SHARE = 0.10
AUDIT_MAX = 5


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end] (inclusive) of each run of True."""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def _band_rows(lo_m: float, hi_m: float, v: float, dt: float, n: int) -> tuple[int, int]:
    return int(round(2 * lo_m / v / dt)), min(int(round(2 * hi_m / v / dt)), n)


def moisture_screen(env: np.ndarray, dt_ns: float, dx_m: float | None, v: float) -> list[dict[str, Any]]:
    """Stretches where deep energy / shallow energy is clearly lower than the line's median.

    env: Hilbert envelope (samples, traces) on the amplitude-preserving track. Returns dicts with
    trace_start, trace_end, drop_db (how much lower than the median), rows of the two bands.
    """
    n, ntr = env.shape
    if ntr < 10 or n < 20:
        return []
    s0, s1 = _band_rows(*SHALLOW_M, v, dt_ns, n)
    d0, d1 = _band_rows(*DEEP_M, v, dt_ns, n)
    if d1 - d0 < 5 or s1 - s0 < 3:  # short time window: use fractions of it instead
        s0, s1, d0, d1 = int(0.08 * n), int(0.3 * n), int(0.3 * n), int(0.85 * n)
    eps = float(np.percentile(env, 50)) * 1e-3 + 1e-12
    shallow = env[s0:s1].mean(axis=0) + eps
    deep = env[d0:d1].mean(axis=0) + eps
    ratio = 20.0 * np.log10(deep / shallow)
    w = max(3, int(round(2.0 / dx_m)) if dx_m else 15)
    k = np.ones(w) / w
    sm = np.convolve(np.pad(ratio, (w // 2, w - 1 - w // 2), mode="edge"), k, mode="valid")
    ref = float(np.median(sm))
    mad = float(np.median(np.abs(sm - ref))) * 1.4826
    thr = ref - max(MIN_DROP_DB, 2.0 * mad)
    min_len = max(5, int(math.ceil(MIN_LEN_M / dx_m)) if dx_m else 10)
    runs = [(a, b) for a, b in _runs(sm < thr)]
    merged: list[list[int]] = []
    for a, b in runs:  # join runs split by a short gap
        if merged and a - merged[-1][1] <= w:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        if b - a + 1 < min_len:
            continue
        out.append({"trace_start": int(a), "trace_end": int(b), "drop_db": round(ref - float(sm[a:b + 1].mean()), 1),
                    "rows": [s0, d1]})
    out.sort(key=lambda x: -x["drop_db"] * (x["trace_end"] - x["trace_start"] + 1) ** 0.5)
    return out[:MAX_SCREEN_PER_LINE]


def _cached_screen(ln: "LineState") -> list[dict[str, Any]]:
    key = "triage_moisture"
    if key not in ln._cache:
        res = ln.result
        src = res.envelope if res.envelope is not None else res.analysis
        ln._cache[key] = moisture_screen(np.abs(src.data), src.dt_ns, src.dx_m, res.velocity_m_per_ns)
    return ln._cache[key]


def _audit_windows(ln: "LineState", busy: list[tuple[int, int]]) -> list[tuple[int, int]]:
    a = ln.analysis
    # 10 m windows on long lines; short lines (a 30 m road scan) get 20 windows
    w = max(10, min(int(round(AUDIT_WINDOW_M / a.dx_m)) if a.dx_m else 50, a.n_traces // 20))
    starts = [s for s in range(0, a.n_traces - w + 1, w)
              if not any(s <= e and b <= s + w - 1 for b, e in busy)]
    n = min(AUDIT_MAX, int(math.ceil(len(starts) * AUDIT_SHARE)))
    rng = random.Random(zlib.crc32(f"{ln.line_id}/{ln.source_file}".encode()))  # same picks every time
    return [(s, s + w - 1) for s in sorted(rng.sample(starts, n))] if n else []


def _point(ln: "LineState", trace: int) -> tuple[float | None, float | None]:
    pos = ln.positions()
    if pos is None:
        return None, None
    lat, lon = pos
    i = min(max(int(trace), 0), len(lat) - 1)
    return round(float(lat[i]), 7), round(float(lon[i]), 7)


def _dist(ln: "LineState", trace: int) -> float | None:
    dx = ln.analysis.dx_m
    return round((trace + ln.trace_offset) * dx, 2) if dx else None


def _depth(ln: "LineState", t_ns: float) -> float:
    return round(ln.result.velocity_m_per_ns * t_ns / 2.0, 2)


def line_items(job: "Job", ln: "LineState") -> list[dict[str, Any]]:
    a = ln.analysis
    T = a.n_samples * a.dt_ns
    items: list[dict[str, Any]] = []
    busy: list[tuple[int, int]] = []
    for z in ln.zones:
        if z.suspected_type != "layer":  # a layer often runs the whole line; it does not make a stretch "found"
            busy.append((z.trace_start, z.trace_end))
        if z.suspected_type == "artifact" or ln.excluded(z):
            continue
        status = {"auto": "todo", "accepted": "confirm", "edited": "confirm"}.get(z.review)
        if status is None:  # rejected
            status = "unusable" if (z.note or "").startswith(UNUSABLE_NOTE) else "normal"
        act = z.action or suggest_action(z, job.info.surface_use)
        items.append({"kind": "zone", "id": z.zone_id, "target": TARGET_OF_TYPE.get(z.suspected_type, "other"),
                      "type": z.suspected_type, "confidence": z.confidence, "action": act, "status": status,
                      "manual": z.zone_id not in ln.auto_zones,
                      "trace_start": z.trace_start, "trace_end": z.trace_end,
                      "time_top_ns": z.time_top_ns, "time_bottom_ns": z.time_bottom_ns,
                      "depth_top_m": z.depth_top_m, "depth_bottom_m": z.depth_bottom_m,
                      "evidence": [e.text or e.code for e in z.evidence][:6], "note": z.note})
    sections = [(s["trace_start"], s["trace_end"]) for s in ln.sections]
    in_section = lambda b, e: any(s0 <= (b + e) / 2 <= s1 for s0, s1 in sections)  # noqa: E731
    for m in _cached_screen(ln):
        b, e = m["trace_start"], m["trace_end"]
        busy.append((b, e))
        if in_section(b, e):
            continue
        sid = f"W{b}"
        top, bot = m["rows"][0] * a.dt_ns, min(m["rows"][1] * a.dt_ns, T)
        long_enough = a.dx_m is not None and (e - b + 1) * a.dx_m >= 10.0
        items.append({"kind": "screen", "id": sid, "target": "moisture", "type": "moisture",
                      "confidence": "medium" if m["drop_db"] >= 6.0 and long_enough else "low", "action": "3",
                      "drop_db": m["drop_db"], "trace_start": b, "trace_end": e, "time_top_ns": round(top, 2),
                      "time_bottom_ns": round(bot, 2), "depth_top_m": _depth(ln, top), "depth_bottom_m": _depth(ln, bot),
                      "evidence": [f"สัญญาณที่ลึกอ่อนกว่าค่ากลางของเส้น {m['drop_db']} dB (ลดทอนเร็ว)",
                                   "อาจเป็นดินชื้น ดินเหนียว หรือหินโรยทางสกปรก ต้องดูภาพยืนยัน"], "note": ""})
    for b, e in _audit_windows(ln, busy):
        if in_section(b, e):
            continue
        items.append({"kind": "audit", "id": f"Q{b}", "target": "audit", "type": "audit", "confidence": "low",
                      "action": "none", "trace_start": b, "trace_end": e, "time_top_ns": 0.0,
                      "time_bottom_ns": round(T, 2), "depth_top_m": 0.0, "depth_bottom_m": _depth(ln, T),
                      "evidence": ["ช่วงที่โปรแกรมไม่พบอะไร สุ่มมาให้ตรวจ"], "note": ""})
    for it in items:
        if it["kind"] != "zone":
            d = job.screen_reviews.get(f"{ln.line_id}/{it['id']}")
            it["status"] = d["decision"] if d else "todo"
            it["note"] = d.get("note", "") if d else ""
        c = (it["trace_start"] + it["trace_end"]) // 2
        it["line_id"] = ln.line_id
        it["lat"], it["lon"] = _point(ln, c)
        it["distance_start_m"], it["distance_end_m"] = _dist(ln, it["trace_start"]), _dist(ln, it["trace_end"])
    return items


UNUSABLE_NOTE = "ภาพใช้ไม่ได้"


def _priority(it: dict[str, Any]) -> tuple:
    # layers last among the candidates (one railway line can give hundreds), random audit picks at the end
    kind = 2 if it["kind"] == "audit" else 1 if it["target"] == "layer" else 0
    return (kind, ACTION_RANK.get(str(it["action"]), 4), CONF_RANK.get(it["confidence"], 3),
            -float(it.get("drop_db") or 0), it["line_id"], it["trace_start"])


def build_queue(job: "Job") -> dict[str, Any]:
    items = [it for ln in job.lines if ln.result is not None for it in line_items(job, ln)]
    items.sort(key=_priority)
    counts = {t: {"total": 0, "todo": 0} for t in TARGETS}
    for it in items:
        counts[it["target"]]["total"] += 1
        counts[it["target"]]["todo"] += it["status"] == "todo"
    return {"items": items, "counts": counts, "decided": sum(it["status"] != "todo" for it in items),
            "total": len(items)}
