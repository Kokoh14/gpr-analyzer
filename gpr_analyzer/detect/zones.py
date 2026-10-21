"""Anomaly zones: connected regions of high depth-normalised envelope, as bounding boxes.

Threshold the contrast section, mask artifact columns and the direct wave, close small gaps,
label connected regions and keep each as a Zone with trace / time / depth / distance extents.
Classification happens later (classify.py); a zone here is only "something stands out".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_closing, binary_opening, find_objects, label

from gpr_analyzer.core import Radargram
from gpr_analyzer.detect._signal import contrast_section
from gpr_analyzer.detect.models import ArtifactFlag, Zone

MASKED_ARTIFACTS = ("vertical_stripe", "dead_trace", "clipped_trace")


@dataclass
class ZoneParams:
    contrast_threshold: float = 4.0
    close_periods: float = 3.0  # vertical gap (in dominant periods) bridged inside one zone
    close_traces: int = 5
    min_traces: int = 3
    min_periods: float = 0.5
    artifact_margin_traces: int = 10
    time_zero_ns: float = 0.0


def zone_id(i: int) -> str:
    """0 → A, 25 → Z, 26 → AA …"""
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def artifact_mask(n_traces: int, artifacts: list[ArtifactFlag] | None) -> np.ndarray:
    cols = np.zeros(n_traces, dtype=bool)
    for a in artifacts or []:
        if a.kind in MASKED_ARTIFACTS:
            cols[a.trace_start:a.trace_end + 1] = True
    return cols


def find_zones(r: Radargram, artifacts: list[ArtifactFlag] | None = None,
               velocity_m_per_ns: float | None = None, velocity_source: str | None = None,
               params: ZoneParams | None = None,
               contrast: tuple[np.ndarray, int, int] | None = None) -> list[Zone]:
    p = params or ZoneParams()
    c, per, top = contrast_section(r.data) if contrast is None else contrast
    n_s, n_t = c.shape
    v = velocity_m_per_ns if velocity_m_per_ns is not None else r.velocity_m_per_ns
    v_src = velocity_source or ("given" if velocity_m_per_ns is not None else "radargram (header/default)")

    raw = c >= p.contrast_threshold
    raw[:top] = False
    raw[:, artifact_mask(n_t, artifacts)] = False
    raw = binary_opening(raw, structure=np.ones((max(1, per // 2), 2)))
    closed = binary_closing(raw, structure=np.ones((int(p.close_periods * per) | 1, p.close_traces)))
    closed &= ~artifact_mask(n_t, artifacts)[None, :]
    closed[:top] = False
    lab, n = label(closed)
    boxes = []
    for k, sl in enumerate(find_objects(lab), 1):
        if sl is None:
            continue
        s_sl, x_sl = sl
        if x_sl.stop - x_sl.start < p.min_traces or s_sl.stop - s_sl.start < p.min_periods * per:
            continue
        comp = lab[sl] == k
        boxes.append((x_sl.start, x_sl.stop - 1, s_sl.start, s_sl.stop - 1,
                      float(np.max(np.where(comp, c[sl], 0.0)))))
    boxes = merge_overlapping(boxes)
    boxes.sort(key=lambda b: (b[0], b[2]))

    zones = []
    for i, (x0, x1, s0, s1, peak) in enumerate(boxes):
        t0 = s0 * r.dt_ns - p.time_zero_ns
        t1 = s1 * r.dt_ns - p.time_zero_ns
        dx = r.dx_m
        z = Zone(
            id=zone_id(i), trace_start=int(x0), trace_end=int(x1), sample_start=int(s0), sample_end=int(s1),
            time_start_ns=float(s0 * r.dt_ns), time_end_ns=float(s1 * r.dt_ns),
            depth_top_m=v * max(t0, 0.0) / 2.0, depth_bottom_m=v * max(t1, 0.0) / 2.0,
            velocity_m_per_ns=float(v), velocity_source=v_src,
            distance_start_m=None if dx is None else x0 * dx,
            distance_end_m=None if dx is None else x1 * dx,
            width_m=None if dx is None else (x1 - x0 + 1) * dx,
            thickness_m=v * (t1 - t0) / 2.0, peak_contrast=round(peak, 3))
        z.nearby_artifacts = [a.id for a in artifacts or [] if _near(a, z, p.artifact_margin_traces)]
        zones.append(z)
    return zones


def merge_overlapping(boxes: list[tuple]) -> list[tuple]:
    """Merge (x0, x1, s0, s1, peak) boxes that overlap: one zone per area of the section."""
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list[tuple] = []
        for b in boxes:
            for i, o in enumerate(out):
                if b[0] <= o[1] and o[0] <= b[1] and b[2] <= o[3] and o[2] <= b[3]:
                    out[i] = (min(b[0], o[0]), max(b[1], o[1]), min(b[2], o[2]), max(b[3], o[3]), max(b[4], o[4]))
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def _near(a: ArtifactFlag, z: Zone, margin: int) -> bool:
    if a.kind == "bottom_noise":
        return z.time_end_ns >= a.time_start_ns
    m = max(margin, z.n_traces // 10)
    return a.overlaps_traces(z.trace_start, z.trace_end, m)
