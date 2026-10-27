"""Anomaly zones: connected regions of high depth-normalised envelope, as bounding boxes.

Threshold the contrast section, mask artifact columns and the direct wave, close small gaps,
label connected regions and keep each as a Zone with trace / time / depth / distance extents.
Classification happens later (classify.py); a zone here is only "something stands out".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_closing, binary_opening, find_objects, label

from gpr_analyzer.core import Radargram, depth_from_time
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
    layer_min_m: float = 1e9  # … or longer than this
    limb_core_fraction: float = 0.25  # keep this share of a hyperbola's half-width; cut the limbs beyond
    max_layer_dip: float = 0.3  # samples per trace
    max_zone_fraction: float = 0.5  # a local (non-layer) zone wider than this share of the line is split up
    min_width_fresnel: float = 1.0  # zones without a hyperbola apex narrower than this × Fresnel radius are dropped


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
    if r.dx_m:  # on long lines a layer is long in metres well before it spans half the line
        layer_len = min(layer_len, max(p.close_traces + 1, int(p.layer_min_m / r.dx_m)))
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
        if not is_layer:
            mask = split_wide(mask, c, p.contrast_threshold, int(p.max_zone_fraction * n_t), hyperbolas, r.dt_ns, per)
        boxes += _boxes(mask, c, is_layer, p.min_traces, p.min_periods * per)
    boxes = merge_overlapping(boxes, apexes=[(h.apex_trace, int(round(h.apex_time_ns / r.dt_ns)))
                                             for h in hyperbolas or [] if not h.one_sided])
    boxes = [b for b in boxes if b[5] or _wide_enough(b, r, v, per, hyperbolas or [], p)]
    boxes.sort(key=lambda b: (b[0], b[2]))

    zones = []
    for i, (x0, x1, s0, s1, peak, is_layer) in enumerate(boxes):
        t0 = s0 * r.dt_ns - p.time_zero_ns
        t1 = s1 * r.dt_ns - p.time_zero_ns
        dx = r.dx_m
        depths = depth_from_time([max(t0, 0.0), max(t1, 0.0)], v, r.metadata.get("antenna_separation_m") or 0.0)
        z = Zone(
            id=zone_id(i), trace_start=int(x0), trace_end=int(x1), sample_start=int(s0), sample_end=int(s1),
            time_start_ns=float(s0 * r.dt_ns), time_end_ns=float(s1 * r.dt_ns),
            depth_top_m=float(depths[0]), depth_bottom_m=float(depths[1]),
            velocity_m_per_ns=float(v), velocity_source=v_src,
            distance_start_m=None if dx is None else x0 * dx,
            distance_end_m=None if dx is None else x1 * dx,
            width_m=None if dx is None else (x1 - x0 + 1) * dx,
            thickness_m=float(depths[1] - depths[0]), peak_contrast=round(peak, 3))
        z.nearby_artifacts = [a.id for a in artifacts or [] if _near(a, z, p.artifact_margin_traces)]
        if is_layer:
            z.features["long_reflector"] = True  # traced as one straight reflector over ≥ layer length
        zones.append(z)
    return zones


def _boxes(mask: np.ndarray, c: np.ndarray, is_layer: bool, min_traces: int, min_samples: float) -> list[tuple]:
    lab, _ = label(mask)
    out = []
    for k, sl in enumerate(find_objects(lab), 1):
        if sl is None:
            continue
        s_sl, x_sl = sl
        if x_sl.stop - x_sl.start < min_traces or s_sl.stop - s_sl.start < min_samples:
            continue
        comp = lab[sl] == k
        out.append((x_sl.start, x_sl.stop - 1, s_sl.start, s_sl.stop - 1,
                    float(np.max(np.where(comp, c[sl], 0.0))), is_layer))
    return out


def split_wide(closed: np.ndarray, c: np.ndarray, threshold: float, max_traces: int,
               hyperbolas: list[Hyperbola] | None = None, dt_ns: float = 1.0, per: int = 1) -> np.ndarray:
    """Components that glue separate events together are cut back to their brighter parts.

    Dense clutter (stony fill, roots) chains the limbs of many reflections into one network. A box
    around it says nothing about where anything is. A component is split when it is wider than
    max_traces, or when it holds two or more full hyperbola apexes at different times (two objects at
    different depths; diffractions from the two edges of one cavity roof are at the same time). The
    threshold is raised step by step inside it until no piece is like that; the weaker limbs that
    joined them drop out, the bright cores stay.
    """
    apexes = [(h.apex_trace, int(round(h.apex_time_ns / dt_ns))) for h in hyperbolas or [] if not h.one_sided]

    def too_big(piece_lab: np.ndarray, sl: tuple) -> bool:
        for k, o in enumerate(find_objects(piece_lab), 1):
            if o is None:
                continue
            if o[1].stop - o[1].start > max_traces:
                return True
            inside = [s for x, s in apexes if o[0].start <= s - sl[0].start < o[0].stop
                      and o[1].start <= x - sl[1].start < o[1].stop and piece_lab[s - sl[0].start, x - sl[1].start] == k]
            if len(inside) >= 2 and max(inside) - min(inside) > 2 * per:
                return True
        return False

    lab, _ = label(closed)
    out = closed
    for k, sl in enumerate(find_objects(lab), 1):
        if sl is None:
            continue
        comp = lab[sl] == k
        if not too_big(np.where(comp, 1, 0), sl):
            continue
        if out is closed:
            out = closed.copy()
        piece = comp
        for f in (1.5, 2, 3, 4, 6, 8, 12, 16):
            piece = comp & binary_closing(c[sl] >= f * threshold, structure=np.ones((max(1, per) | 1, 3)))
            if not too_big(label(piece)[0], sl):
                break
        out[sl] = np.where(comp, piece, out[sl])
    return out


def fresnel_radius_m(depth_m: float, wavelength_m: float) -> float:
    """First Fresnel zone radius: the lateral resolution of a reflection at that depth."""
    return float(np.sqrt(wavelength_m * max(depth_m, 0.0) / 2 + wavelength_m**2 / 16))


def _wide_enough(b: tuple, r: Radargram, v: float, per: int, hyperbolas: list[Hyperbola], p: ZoneParams) -> bool:
    """A reflection narrower than the Fresnel zone at its depth cannot come from a real interface or
    object of its own; such specks are noise or bits of a larger event. Zones holding a hyperbola apex
    are always kept."""
    x0, x1, s0, s1 = b[:4]
    if r.dx_m is None or p.min_width_fresnel <= 0:
        return True
    if any(x0 <= h.apex_trace <= x1 and s0 * r.dt_ns - per * r.dt_ns <= h.apex_time_ns <= s1 * r.dt_ns
           for h in hyperbolas):
        return True
    t0 = max(s0 * r.dt_ns - p.time_zero_ns, 0.0)
    depth = float(depth_from_time([t0], v, r.metadata.get("antenna_separation_m") or 0.0)[0])
    wavelength = v * per * r.dt_ns
    return (x1 - x0 + 1) * r.dx_m >= p.min_width_fresnel * fresnel_radius_m(depth, wavelength)


def _row_runs_at_least(mask: np.ndarray, length: int) -> np.ndarray:
    """Pixels that belong to a horizontal run of True of at least `length` (per row)."""
    n_s, n_t = mask.shape
    out = np.zeros_like(mask)
    cand = np.flatnonzero(np.count_nonzero(mask, axis=1) >= length)  # other rows cannot hold such a run
    if not cand.size:
        return out
    # candidate rows laid end to end with one False between them, so runs never continue into the next row
    flat = np.zeros((cand.size, n_t + 1), dtype=np.int8)
    flat[:, :n_t] = mask[cand]
    d = np.diff(flat.ravel(), prepend=np.int8(0))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    keep = ends - starts >= length
    edge = np.zeros(flat.size + 1, dtype=np.int32)
    edge[starts[keep]] += 1
    edge[ends[keep]] -= 1
    out[cand] = (np.cumsum(edge[:-1]) > 0).reshape(cand.size, n_t + 1)[:, :n_t]
    return out


def _shifted_rows(padded: np.ndarray, pad: int, n_s: int, shift: np.ndarray) -> np.ndarray:
    """Rows pad - shift[x] .. pad - shift[x] + n_s of each column x of `padded`.

    shift changes only now and then along x, so whole blocks of columns are copied at once."""
    out = np.empty((n_s, padded.shape[1]), dtype=padded.dtype)
    edges = np.concatenate(([0], np.flatnonzero(np.diff(shift)) + 1, [shift.size]))
    for a, b in zip(edges[:-1], edges[1:]):
        r = pad - int(shift[a])
        out[:, a:b] = padded[r:r + n_s, a:b]
    return out


def long_reflectors(mask: np.ndarray, length: int, max_dip: float, n_slopes: int | None = None) -> np.ndarray:
    """Pixels on straight reflectors at least `length` traces long, horizontal or gently dipping.

    The mask is sheared for each trial slope so a dipping line becomes a row, then horizontal
    runs are measured. Cheaper than a morphological opening with long tilted lines. By default the
    slopes are spaced so that a reflector between two trial slopes drifts by at most about one row
    over `length` traces (the ±1 row wobble allowed below); a coarser spacing misses dipping layers.
    """
    n_s, n_t = mask.shape
    out = np.zeros_like(mask)
    x = np.arange(n_t)
    if n_slopes is None:
        n_slopes = int(np.ceil(2 * max_dip * length / 1.5)) + 1
    if not mask.any():
        return out
    slopes = np.linspace(-max_dip, max_dip, n_slopes)
    shifts = [np.round(slope * (x - n_t / 2)).astype(int) for slope in slopes]
    pad = max(int(np.abs(sh).max()) for sh in shifts) + 1
    padded = np.zeros((n_s + 2 * pad, n_t), dtype=mask.dtype)  # rows outside the section read as False
    padded[pad:pad + n_s] = mask
    padded_long = np.zeros_like(padded)
    for shift in shifts:
        sheared = _shifted_rows(padded, pad, n_s, shift)  # pixel (i, x) moves to row i + shift[x]
        # a run in the sheared image allows ±1 row of wobble
        thick = sheared | np.roll(sheared, 1, axis=0) | np.roll(sheared, -1, axis=0)
        padded_long[pad:pad + n_s] = _row_runs_at_least(thick, length) & sheared
        out |= _shifted_rows(padded_long, pad, n_s, -shift) & mask
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


def _overlap_share(a: tuple, b: tuple) -> float:
    """Overlap area of two boxes as a share of the smaller one."""
    w = min(a[1], b[1]) - max(a[0], b[0]) + 1
    h = min(a[3], b[3]) - max(a[2], b[2]) + 1
    if w <= 0 or h <= 0:
        return 0.0
    area = min((a[1] - a[0] + 1) * (a[3] - a[2] + 1), (b[1] - b[0] + 1) * (b[3] - b[2] + 1))
    return w * h / area


def merge_overlapping(boxes: list[tuple], min_share: float = 0.3,
                      apexes: list[tuple[int, int]] | None = None) -> list[tuple]:
    """Merge (x0, x1, s0, s1, peak, is_layer) boxes that overlap: one zone per area of the section.

    Boxes holding a full hyperbola apex (trace, sample) merge only when they overlap by at least
    min_share of the smaller box: in stony ground the boxes around crossing limbs touch each other
    all along the line, and merging every touch would chain them into one box across the line.
    Long layers are only merged with each other, never with the local zones they cross."""
    apexes = apexes or []

    def has_apex(b: tuple) -> bool:
        return any(b[0] <= x <= b[1] and b[2] <= s <= b[3] for x, s in apexes)

    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list[tuple] = []
        for b in boxes:
            for i, o in enumerate(out):
                share = _overlap_share(b, o)
                if b[5] == o[5] and share > 0 and (share >= min_share or not (has_apex(b) or has_apex(o))):
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
    if a.kind == "ringing":
        return z.time_start_ns <= a.time_end_ns and a.time_start_ns <= z.time_end_ns
    m = max(margin, z.n_traces // 5)
    return a.overlaps_traces(z.trace_start, z.trace_end, m)
