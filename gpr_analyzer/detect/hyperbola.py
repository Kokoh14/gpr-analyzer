"""Diffraction-hyperbola candidates and velocity from x²–t² fitting.

For a point at depth z and horizontal offset d, two-way time is t(d) = (2/v)·sqrt(z² + d²), so
t² = t0² + (4/v²)·d² is a straight line in (d², t²). Each candidate apex (a local maximum of the
depth-normalised envelope) is followed down both limbs inside the time window allowed by a
plausible velocity range; the picks are fitted by least squares and kept when the line is good,
the velocity is plausible and there is real moveout (a flat layer has none).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import maximum_filter

from gpr_analyzer.core import Radargram
from gpr_analyzer.detect._signal import contrast_section
from gpr_analyzer.detect.models import Hyperbola, VelocityEstimate


@dataclass
class HyperbolaParams:
    v_min: float = 0.03  # m/ns, wet clay
    v_max: float = 0.20  # m/ns, very dry; air (0.3) is not a host medium
    contrast_threshold: float = 4.0
    max_half_width: int = 40  # traces each side
    min_picks_per_side: int = 4
    min_picks_total: int = 10
    pick_ratio: float = 0.3  # limb pick must keep this share of the apex contrast
    max_misses: int = 3
    min_r2: float = 0.9
    min_moveout_periods: float = 0.75
    max_candidates: int = 300
    time_zero_ns: float = 0.0


def _follow_limb(c, x0, s0, step, p, dt, dx, t0_ns, per):
    """Follow one limb outward from the apex. Returns list of (offset_m, t_ns).

    Each pick must lie inside the time window allowed by [v_min, v_max] *relative to the
    previous pick*, so the limb cannot jump onto a stronger reflector further down.
    """
    n_s, n_t = c.shape
    apex = c[s0, x0]

    def window(k):
        dist = k * dx
        return (np.sqrt(t0_ns**2 + (2 * dist / p.v_max) ** 2), np.sqrt(t0_ns**2 + (2 * dist / p.v_min) ** 2))

    picks, misses = [], 0
    prev_k, prev_t = 0, t0_ns
    tol = max(1.0, per / 4) * dt
    for k in range(1, p.max_half_width + 1):
        x = x0 + step * k
        if x < 0 or x >= n_t:
            break
        lo_k, hi_k = window(k)
        lo_p, hi_p = window(prev_k)
        lo = max(prev_t, max(lo_k, prev_t + (lo_k - lo_p)) - tol) + p.time_zero_ns
        hi = min(hi_k, prev_t + (hi_k - hi_p)) + tol + p.time_zero_ns
        a = max(1, int(np.floor(lo / dt)))
        b = min(n_s - 2, int(np.ceil(hi / dt)))
        if b < a:
            break
        j = int(np.argmax(c[a:b + 1, x]))
        s = a + j
        interior = c[s - 1, x] < c[s, x] >= c[s + 1, x]
        if interior and c[s, x] >= p.pick_ratio * apex and c[s, x] >= 0.5 * p.contrast_threshold:
            t = s * dt - p.time_zero_ns
            picks.append((k * dx, t))
            prev_k, prev_t = k, t
            misses = 0
        else:
            misses += 1
            if misses >= p.max_misses:
                break
    return picks


def find_hyperbolas(r: Radargram, params: HyperbolaParams | None = None,
                    contrast: tuple[np.ndarray, int, int] | None = None) -> list[Hyperbola]:
    p = params or HyperbolaParams()
    c, per, top = contrast_section(r.data) if contrast is None else contrast
    n_s, n_t = c.shape
    dx_known = r.dx_m is not None
    dx = r.dx_m if dx_known else 1.0
    pp = p
    if not dx_known:  # velocity unknown in m/ns: accept any curvature, report v=None
        pp = HyperbolaParams(**{**p.__dict__, "v_min": 1e-3, "v_max": 1e3})

    # 3 % tolerance: near the apex the hyperbola is flat and noise decides which trace is highest
    peaks = (c >= 0.97 * maximum_filter(c, size=(2 * per + 1, 2 * per + 1))) & (c >= p.contrast_threshold)
    peaks[:top] = False
    ss, xs = np.nonzero(peaks)
    order = np.argsort(-c[ss, xs])[: p.max_candidates]
    found: list[Hyperbola] = []
    taken = np.zeros_like(c, dtype=bool)
    for i in order:
        s0, x0 = int(ss[i]), int(xs[i])
        if taken[s0, x0]:
            continue
        t0 = s0 * r.dt_ns - p.time_zero_ns
        if t0 <= 0:
            continue
        left = _follow_limb(c, x0, s0, -1, pp, r.dt_ns, dx, t0, per)
        right = _follow_limb(c, x0, s0, +1, pp, r.dt_ns, dx, t0, per)
        min_mo = p.min_moveout_periods * per * r.dt_ns

        def limb_ok(side):  # a flat side (cavity roof, layer) is not a limb
            return len(side) >= p.min_picks_per_side and side[-1][1] - t0 >= min_mo

        left = left if limb_ok(left) else []
        right = right if limb_ok(right) else []
        both = bool(left) and bool(right)
        # one limb only: line ends, or the edge of a cavity roof (the other side is the flat roof)
        one_sided = not both and max(len(left), len(right)) >= p.min_picks_total
        if not (both or one_sided) or len(left) + len(right) < p.min_picks_total:
            continue
        pts = np.array([(0.0, t0)] + left + right)
        d2, t2 = pts[:, 0] ** 2, pts[:, 1] ** 2
        slope, icpt = np.polyfit(d2, t2, 1)
        pred = slope * d2 + icpt
        ss_res = float(np.sum((t2 - pred) ** 2))
        ss_tot = float(np.sum((t2 - t2.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        moveout = float(pts[:, 1].max() - t0)
        if slope <= 0 or r2 < p.min_r2 or moveout < min_mo:
            continue
        v = 2.0 / np.sqrt(slope)
        if dx_known and not (p.v_min <= v <= p.v_max):
            continue
        half = max(len(left), len(right))
        a, b = max(0, x0 - half), min(n_t, x0 + half + 1)
        lo = max(0, s0 - per)
        hi = min(n_s, int(pts[:, 1].max() / r.dt_ns) + per + 1)
        taken[lo:hi, a:b] = True  # one hyperbola per apex region; limbs are not new apexes
        vel = float(v) if dx_known else None
        found.append(Hyperbola(
            apex_trace=x0, apex_time_ns=float(t0 + p.time_zero_ns), amplitude=float(c[s0, x0]),
            fit_r2=round(r2, 4), n_picks=len(pts), half_width_traces=half, velocity_m_per_ns=vel,
            apex_depth_m=(vel * t0 / 2.0) if vel else None, one_sided=one_sided))
    found.sort(key=lambda h: (h.apex_trace, h.apex_time_ns))
    return found


def estimate_velocity(r: Radargram, params: HyperbolaParams | None = None,
                      hyperbolas: list[Hyperbola] | None = None, min_fits: int = 1) -> VelocityEstimate | None:
    """Median velocity of the accepted hyperbola fits, or None if there are none."""
    hs = find_hyperbolas(r, params) if hyperbolas is None else hyperbolas
    vs = np.array([h.velocity_m_per_ns for h in hs if h.velocity_m_per_ns])
    if vs.size < min_fits or vs.size == 0:
        return None
    q1, q3 = np.percentile(vs, [25, 75])
    return VelocityEstimate(float(np.median(vs)), float((q3 - q1) / 2), int(vs.size))
