"""Cross-line check: is a zone seen again on the neighbouring lines of the survey?

One 2-D line cannot tell a stone from a pipe, or a cavity from a local lens. Neighbouring parallel or
crossing lines can: a cavity wider than the line spacing must show on the next line at about the same
depth, a pipe crossing the lines shows on each of them, a stone shows on one. Positions come from the
GPS of each line (ImpulseRadar .cor), so the check is only as good as the GPS.

Rules (defaults in CrossLineParams; uncalibrated, chosen to be conservative):

* Only suspicious zones are checked: void-like, scattered, pipe / point, unknown. Layers and artifacts
  are left alone.
* A neighbour is another line (another file; the channels of one file share one track) of the same
  channel whose track passes within max_gap_m of the zone centre.
* The neighbour confirms when it has a suspicious zone within the zone half-width + GPS uncertainty +
  match_m of that closest point, with overlapping depth (± max(depth_tol_m, depth_tol_fraction × depth)).
* The neighbour contradicts when it has no such zone although its track passes over the zone's own
  footprint: closest distance + GPS uncertainty ≤ footprint_share × half the zone width along its line
  (the box includes diffraction tails, so the object is taken as narrower than the box, and about as
  wide across the line as along it), and the reviewer has not excluded that part of the neighbour.
* Void-like / scattered / unknown: contradicted and never confirmed → one level lower, with ⚠ reason.
  Confirmed → ✓ reason, level kept (no upgrades until the rules are calibrated on dug ground truth).
* Pipe / point: the level is never changed (a stone is a point object too); the reason says whether it
  repeats on the neighbours (may be a pipe or cable across the lines) or not (isolated object).
* Lines whose GPS path length differs from the wheel distance by more than max_length_mismatch are not
  used: their positions are not good enough to compare lines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

EARTH_RADIUS_M = 6371008.8
SUSPICIOUS = {"void_like", "scattered", "unknown", "unclassified", "pipe_point", "point_target"}
POINT = {"pipe_point", "point_target"}
LOWER = {"high": "medium", "medium": "low", "low": "low"}


@dataclass
class CrossLineParams:
    gps_uncertainty_m: float = 0.5  # horizontal position error of the GPS track (RTK ~0.05, plain GNSS 2–5)
    max_gap_m: float = 2.0  # neighbours farther than this from the zone are not compared
    match_m: float = 0.5  # extra slack along the neighbour when looking for the same zone
    depth_tol_m: float = 0.15
    depth_tol_fraction: float = 0.2
    max_length_mismatch: float = 0.15  # GPS path length vs wheel distance
    footprint_share: float = 0.5  # a zone box includes diffraction tails; its object is taken as this share wide


@dataclass
class LineTrack:
    """One processed line: its id, the file it came from, its channel, trace positions and zones.

    xy: (n_traces, 2) metres in a common local frame (see local_xy), None when the line has no GPS.
    zones: objects with zone_id, trace_start, trace_end, suspected_type, confidence, depth_top_m,
    depth_bottom_m (report.schema.Zone or detect.models.Zone). excluded: trace ranges whose results the
    reviewer does not use, or artifact bands, where a missing zone proves nothing.
    """

    line_id: str
    source: str
    channel: int | None
    xy: np.ndarray | None
    zones: list[Any]
    dx_m: float | None = None
    excluded: list[tuple[int, int]] = field(default_factory=list)
    gps_ok: bool = True


@dataclass
class CrossLineResult:
    status: str  # "confirmed" | "contradicted" | "not_assessed" | "isolated"
    confirmed_by: list[tuple[str, str, float]] = field(default_factory=list)  # (line, zone, distance m)
    contradicted_by: list[tuple[str, float]] = field(default_factory=list)  # (line, distance m)
    new_confidence: str | None = None  # set when the level changes
    text_th: str = ""
    text_en: str = ""
    kind: str = "not_assessed"  # evidence kind: present / absent / warning / not_assessed
    code: str = "cross_line_not_assessed"


def local_xy(lat: np.ndarray, lon: np.ndarray, lat0: float, lon0: float) -> np.ndarray:
    """Equirectangular metres east / north of (lat0, lon0). Fine over a survey a few km across."""
    k = math.radians(1.0) * EARTH_RADIUS_M
    return np.column_stack([(np.asarray(lon) - lon0) * k * math.cos(math.radians(lat0)), (np.asarray(lat) - lat0) * k])


def gps_length_ok(xy: np.ndarray, wheel_length_m: float | None, max_mismatch: float) -> bool:
    """GPS path length agrees with the wheel distance within max_mismatch (a share)."""
    if xy is None or len(xy) < 2:
        return False
    if not wheel_length_m:
        return True
    gps = float(np.sum(np.hypot(*np.diff(xy, axis=0).T)))
    return abs(gps / wheel_length_m - 1.0) <= max_mismatch


def _type(z: Any) -> str:
    t = z.suspected_type
    return getattr(t, "value", t)


def _conf(z: Any) -> str:
    c = z.confidence
    return getattr(c, "value", c)


def _excluded(track: LineTrack, a: int, b: int) -> bool:
    return any(s <= b and a <= e for s, e in track.excluded)


def check_lines(tracks: Iterable[LineTrack], params: CrossLineParams | None = None
                ) -> dict[tuple[str, str], CrossLineResult]:
    """Cross-line result for every suspicious zone, keyed by (line_id, zone_id)."""
    p = params or CrossLineParams()
    tracks = list(tracks)
    out: dict[tuple[str, str], CrossLineResult] = {}
    for a in tracks:
        for z in a.zones:
            if _type(z) not in SUSPICIOUS:
                continue
            out[(a.line_id, z.zone_id)] = _check_zone(a, z, tracks, p)
    return out


def _check_zone(a: LineTrack, z: Any, tracks: list[LineTrack], p: CrossLineParams) -> CrossLineResult:
    if a.xy is None or not a.gps_ok:
        return _not_assessed("ไม่มี GPS ที่เชื่อถือได้ของแนวนี้", "no reliable GPS on this line")
    n = len(a.xy)
    c = min(max((z.trace_start + z.trace_end) // 2, 0), n - 1)
    pc = a.xy[c]
    i0, i1 = min(max(z.trace_start, 0), n - 1), min(max(z.trace_end, 0), n - 1)
    half_w = float(np.hypot(*(a.xy[i1] - a.xy[i0]))) / 2
    depth = 0.5 * (z.depth_top_m + z.depth_bottom_m)
    tol_z = max(p.depth_tol_m, p.depth_tol_fraction * depth)
    res = CrossLineResult(status="not_assessed")
    for b in tracks:
        if b is a or b.source == a.source or b.channel != a.channel or b.xy is None or not b.gps_ok:
            continue
        d = np.hypot(*(b.xy - pc).T)
        j = int(np.argmin(d))
        dist = float(d[j])
        if dist > p.max_gap_m:
            continue
        reach = half_w + p.gps_uncertainty_m + p.match_m
        near = np.flatnonzero(d <= dist + reach)
        lo, hi = int(near.min()), int(near.max())
        match = [y for y in b.zones if _type(y) in SUSPICIOUS and y.trace_start <= hi and lo <= y.trace_end
                 and y.depth_top_m - tol_z <= z.depth_bottom_m and z.depth_top_m <= y.depth_bottom_m + tol_z]
        if match:
            res.confirmed_by.append((b.line_id, match[0].zone_id, round(dist, 2)))
        elif dist + p.gps_uncertainty_m <= p.footprint_share * half_w and not _excluded(b, lo, hi):
            res.contradicted_by.append((b.line_id, round(dist, 2)))
        elif res.status == "not_assessed":
            res.status = "passed_nearby"
    return _verdict(z, res, half_w, p)


def _not_assessed(why_th: str, why_en: str) -> CrossLineResult:
    return CrossLineResult(status="not_assessed", kind="not_assessed", code="cross_line_not_assessed",
                           text_th=f"ตรวจข้ามแนวไม่ได้: {why_th}", text_en=f"Cross-line check not possible: {why_en}")


def _verdict(z: Any, res: CrossLineResult, half_w: float, p: CrossLineParams) -> CrossLineResult:
    point = _type(z) in POINT
    seen = ", ".join(f"{ln} (โซน {zid}, ห่าง {d:.1f} ม.)" for ln, zid, d in res.confirmed_by)
    seen_en = ", ".join(f"{ln} (zone {zid}, {d:.1f} m away)" for ln, zid, d in res.confirmed_by)
    miss = ", ".join(f"{ln} (ห่าง {d:.1f} ม.)" for ln, d in res.contradicted_by)
    miss_en = ", ".join(f"{ln} ({d:.1f} m away)" for ln, d in res.contradicted_by)
    if res.confirmed_by:
        res.status, res.kind, res.code = "confirmed", "present", "cross_line_seen"
        if point:
            res.text_th = f"พบที่ความลึกใกล้เคียงในแนวข้างเคียง {seen}: อาจเป็นท่อหรือสายที่วางตัดแนวสำรวจ"
            res.text_en = f"seen at a similar depth on neighbouring line(s) {seen_en}: may be a pipe or cable across the lines"
        else:
            res.text_th = f"พบโซนที่ความลึกใกล้เคียงในแนวข้างเคียง {seen}"
            res.text_en = f"a zone at a similar depth is seen on neighbouring line(s) {seen_en}"
        return res
    if res.contradicted_by:
        res.status, res.kind, res.code = ("isolated" if point else "contradicted"), "warning", "cross_line_absent"
        if point:
            res.text_th = (f"แนวข้างเคียง {miss} ผ่านใกล้แต่ไม่พบที่ความลึกนี้: น่าจะเป็นวัตถุเดี่ยว (หิน เศษวัสดุ) "
                           "มากกว่าท่อที่วางตัดแนว")
            res.text_en = (f"neighbouring line(s) {miss_en} pass close by without it: more likely an isolated object "
                           "(stone, debris) than a pipe across the lines")
            return res
        res.new_confidence = LOWER[_conf(z)] if LOWER[_conf(z)] != _conf(z) else None
        res.text_th = (f"แนวข้างเคียง {miss} ผ่านเหนือโซนนี้ (กว้าง {2 * half_w:.1f} ม.) แต่ไม่พบโซนที่ความลึกนี้"
                       + (" จึงลดระดับลงหนึ่งขั้น" if res.new_confidence else ""))
        res.text_en = (f"neighbouring line(s) {miss_en} pass over this {2 * half_w:.1f} m wide zone but show nothing "
                       "at this depth" + ("; level lowered by one" if res.new_confidence else ""))
        return res
    if res.status == "passed_nearby":
        res.status, res.kind, res.code = "not_assessed", "not_assessed", "cross_line_not_assessed"
        res.text_th = (f"แนวข้างเคียงอยู่ห่างเกินกว่าความกว้างโซน ({2 * half_w:.1f} ม.) + ความคลาดเคลื่อน GPS "
                       f"({p.gps_uncertainty_m:.1f} ม.) จึงตัดสินไม่ได้ว่าควรเห็นหรือไม่")
        res.text_en = (f"neighbouring lines are farther than the zone width ({2 * half_w:.1f} m) plus GPS error "
                       f"({p.gps_uncertainty_m:.1f} m); cannot tell whether they should show it")
        return res
    return _not_assessed(f"ไม่มีแนวอื่นผ่านภายใน {p.max_gap_m:.1f} ม.", f"no other line within {p.max_gap_m:.1f} m")
