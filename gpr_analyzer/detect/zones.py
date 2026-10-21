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
from gpr_analyzer.detect.models import ArtifactFlag, Hyperbola, Zone

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
    layer_min_fraction: float = 0.5  # reflectors longer than this share of the line become their own zones
    limb_core_fraction: float = 0.25  # keep this share of a hyperbola's half-width; cut the limbs beyond
    max_layer_dip: float = 0.3  # samples per trace


def zone_id(i: int) -> str:
    """0 → A, 25 → Z, 26 → AA …"""
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def bridge_narrow_artifacts(c: np.ndarray, artifacts: list[ArtifactFlag] | None, max_width: int) -> np.ndarray:
    """Replace narrow artifact columns by interpolation from both sides.

    A one-trace stripe through a target would otherwise cut the zone into pieces. Wider bands
    stay masked: there is too little to interpolate across.
    """
    out = c
    n_t = c.shape[1]
    for a in artifacts or []:
        if a.kind not in MASKED_ARTIFACTS or a.trace_end - a.trace_start + 1 > max_width:
            continue
        # contrast_section smooths over 3 traces, so the band bleeds one trace each side
        lo, hi = a.trace_start - 2, a.trace_end + 2
        if lo < 0 or hi >= n_t:
            continue
        if out is c:
            out = c.copy()
        w = np.linspace(0, 1, hi - lo + 1)[1:-1]
        out[:, lo + 1:hi] = out[:, [lo]] * (1 - w) + out[:, [hi]] * w
    return out


def artifact_mask(n_traces: int, artifacts: list[ArtifactFlag] | None, max_width: int = 0) -> np.ndarray:
    """Columns excluded from zones: artifact bands wider than max_width (narrower ones are bridged)."""
    cols = np.zeros(n_traces, dtype=bool)
    for a in artifacts or []:
        if a.kind in MASKED_ARTIFACTS and a.trace_end - a.trace_start + 1 > max_width:
            cols[a.trace_start:a.trace_end + 1] = True
    return cols


def find_zones(r: Radargram, artifacts: list[ArtifactFlag] | None = None,
               velocity_m_per_ns: float | None = None, velocity_source: str | None = None,
               params: ZoneParams | None = None,
               contrast: tuple[np.ndarray, int, int] | None = None,
               hyperbolas: list[Hyperbola] | None = None) -> list[Zone]:
    """Zones as bounding boxes.

    Three things would otherwise glue unrelated events into one box: long hyperbola limbs,
    long layers crossing them, and gained noise below the signal. So limbs beyond the apex core
    are cut, reflectors spanning ≥ layer_min_fraction of the line are separated into their own
    components, and components are not allowed to cross the bottom-noise onset.
    """
    p = params or ZoneParams()
    c, per, top = contrast_section(r.data) if contrast is None else contrast
    n_s, n_t = c.shape
    v = velocity_m_per_ns if velocity_m_per_ns is not None else r.velocity_m_per_ns
    v_src = velocity_source or ("given" if velocity_m_per_ns is not None else "radargram (header/default)")

    c = bridge_narrow_artifacts(c, artifacts, p.close_traces)
    raw = c >= p.contrast_threshold
    raw[:top] = False
    raw[:, artifact_mask(n_t, artifacts, p.close_traces)] = False
    raw = binary_opening(raw, structure=np.ones((max(1, per // 2), 2)))
    layer_len = max(p.close_traces + 1, int(p.layer_min_fraction * n_t))
    layers = long_reflectors(raw, layer_len, p.max_layer_dip)
    rest = raw & ~layers & ~limb_mask(c, hyperbolas or [], r.dt_ns, r.dx_m, per, p)
    struct = np.ones((int(p.close_periods * per) | 1, p.close_traces))
    closed = binary_closing(rest, structure=struct) & ~layers
    layers = binary_closing(layers, structure=np.ones((1, p.close_traces)))
    closed &= ~artifact_mask(n_t, artifacts, p.close_traces)[None, :]
    closed[:top] = False
    for a in artifacts or []:
        if a.kind == "bottom_noise":  # nothing may straddle the noise onset
            k = int(a.time_start_ns / r.dt_ns)
            closed[max(0, k - 1):k + 1] = False
    boxes = []
    for mask, is_layer in ((closed, False), (layers, True)):
        mask = mask & ~artifact_mask(n_t, artifacts, p.close_traces)[None, :]
        mask[:top] = False
        lab, n = label(mask)
        for k, sl in enumerate(find_objects(lab), 1):
            if sl is None:
                continue
            s_sl, x_sl = sl
            if x_sl.stop - x_sl.start < p.min_traces or s_sl.stop - s_sl.start < p.min_periods * per:
                continue
            comp = lab[sl] == k
            boxes.append((x_sl.start, x_sl.stop - 1, s_sl.start, s_sl.stop - 1,
                          float(np.max(np.where(comp, c[sl], 0.0))), is_layer))
    boxes = merge_overlapping(boxes)
    boxes.sort(key=lambda b: (b[0], b[2]))

    zones = []
    for i, (x0, x1, s0, s1, peak, _) in enumerate(boxes):
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


def _row_runs_at_least(mask: np.ndarray, length: int) -> np.ndarray:
    """Pixels that belong to a horizontal run of True of at least `length` (per row)."""
    out = np.zeros_like(mask)
    for i in np.flatnonzero(mask.any(axis=1)):
        row = mask[i]
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        for a, b in zip(starts, ends):
            if b - a >= length:
                out[i, a:b] = True
    return out


def long_reflectors(mask: np.ndarray, length: int, max_dip: float, n_slopes: int = 9) -> np.ndarray:
    """Pixels on straight reflectors at least `length` traces long, horizontal or gently dipping.

    The mask is sheared for each trial slope so a dipping line becomes a row, then horizontal
    runs are measured. Cheaper than a morphological opening with long tilted lines.
    """
    n_s, n_t = mask.shape
    out = np.zeros_like(mask)
    x = np.arange(n_t)
    for slope in np.linspace(-max_dip, max_dip, n_slopes):
        shift = np.round(slope * (x - n_t / 2)).astype(int)
        rows = (np.arange(n_s)[:, None] + shift[None, :])  # sheared row index for each pixel
        valid = (rows >= 0) & (rows < n_s)
        sheared = np.zeros_like(mask)
        sheared[np.where(valid, rows, 0), x[None, :].repeat(n_s, 0)] |= mask & valid
        # a run in the sheared image allows ±1 row of wobble
        thick = sheared | np.roll(sheared, 1, axis=0) | np.roll(sheared, -1, axis=0)
        long_ = _row_runs_at_least(thick, length) & sheared
        back = long_[np.where(valid, rows, 0), x[None, :].repeat(n_s, 0)] & valid
        out |= back & mask
    return out


def limb_mask(c: np.ndarray, hyperbolas: list[Hyperbola], dt_ns: float, dx_m: float | None, per: int,
              p: ZoneParams) -> np.ndarray:
    """Pixels on hyperbola limbs outside the apex core (needs the fitted velocity).

    The fitted curve is followed past the picked half-width for as long as the section is still
    bright along it, so long limbs do not survive as stray zones further out.
    """
    n_s, n_t = c.shape
    m = np.zeros(c.shape, dtype=bool)
    if dx_m is None:
        return m
    for h in hyperbolas:
        if not h.velocity_m_per_ns:
            continue
        core = max(per // 2, int(p.limb_core_fraction * h.half_width_traces))
        t0 = h.apex_time_ns - p.time_zero_ns
        sides = (-1, 1) if h.side == 0 else (h.side,)
        for sd in sides:
            misses = 0
            for k in range(core + 1, n_t):
                x = h.apex_trace + sd * k
                if not 0 <= x < n_t:
                    break
                t = np.sqrt(t0**2 + (2 * k * dx_m / h.velocity_m_per_ns) ** 2) + p.time_zero_ns
                s = int(round(t / dt_ns))
                if s >= n_s:
                    break
                a, b = max(0, s - per), min(n_s, s + per + 1)
                if k > h.half_width_traces:
                    misses = misses + 1 if c[a:b, x].max() < p.contrast_threshold else 0
                    if misses >= 3:
                        break
                m[a:b, x] = True
    return m


def merge_overlapping(boxes: list[tuple]) -> list[tuple]:
    """Merge (x0, x1, s0, s1, peak, is_layer) boxes that overlap: one zone per area of the section.
    Long layers are only merged with each other, never with the local zones they cross."""
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list[tuple] = []
        for b in boxes:
            for i, o in enumerate(out):
                if b[5] == o[5] and b[0] <= o[1] and o[0] <= b[1] and b[2] <= o[3] and o[2] <= b[3]:
                    out[i] = (min(b[0], o[0]), max(b[1], o[1]), min(b[2], o[2]), max(b[3], o[3]),
                              max(b[4], o[4]), b[5])
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def _near(a: ArtifactFlag, z: Zone, margin: int) -> bool:
    if a.kind == "bottom_noise":
        return z.time_end_ns >= a.time_start_ns
    m = max(margin, z.n_traces // 5)
    return a.overlaps_traces(z.trace_start, z.trace_end, m)
