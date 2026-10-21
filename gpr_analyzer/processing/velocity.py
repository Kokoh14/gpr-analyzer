"""Wave velocity from diffraction hyperbolas (x^2 - t^2 fit).

For a point diffractor at (x0, depth d) a zero-offset antenna records
    t(x)^2 = t0^2 + 4 (x - x0)^2 / v^2,   t0 = 2 d / v.
Expanding, t^2 = a + b x + c x^2 is linear in (a, b, c), so a least-squares fit
gives v = 2 / sqrt(c), x0 = -b / (2c), t0 = sqrt(a - b^2 / (4c)) directly.

Picks come from a velocity scan around each strong envelope peak, then the fit
refines them. A fit gives velocity and apex only; it does not give pipe radius.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
from scipy import ndimage

from gpr_analyzer.processing.filters import envelope

V_MIN = 0.03  # m/ns, wet clay / water is ~0.033
V_MAX = 0.3  # m/ns, air


@dataclass
class HyperbolaFit:
    x0_m: float
    t0_ns: float
    velocity_m_per_ns: float
    n_picks: int
    rms_residual_ns: float
    r2: float

    @property
    def depth_m(self) -> float:
        return self.velocity_m_per_ns * self.t0_ns / 2.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["depth_m"] = self.depth_m
        return d


def fit_hyperbola(x_m: np.ndarray, t_ns: np.ndarray) -> HyperbolaFit:
    """Least-squares x^2 - t^2 fit. Raises ValueError if the picks are not a downward-opening hyperbola."""
    x = np.asarray(x_m, dtype=np.float64)
    t = np.asarray(t_ns, dtype=np.float64)
    if x.size < 5:
        raise ValueError("need at least 5 picks")
    xc = x.mean()
    xs = x - xc
    A = np.column_stack([np.ones_like(xs), xs, xs**2])
    y = t**2
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    a, b, c = coef
    if c <= 0:
        raise ValueError("picks are flat or curve the wrong way")
    v = 2.0 / np.sqrt(c)
    if v > V_MAX * 1.01:
        raise ValueError(f"fitted velocity {v:.3g} m/ns is faster than light: picks too flat")
    x0 = -b / (2.0 * c)
    t0sq = a - b * b / (4.0 * c)
    if t0sq <= 0:
        raise ValueError("apex above time zero")
    t0 = np.sqrt(t0sq)
    t_model = np.sqrt(np.maximum(A @ coef, 0.0))
    resid = t - t_model
    ss_tot = np.sum((t - t.mean()) ** 2)
    r2 = 1.0 - np.sum(resid**2) / ss_tot if ss_tot > 0 else 0.0
    return HyperbolaFit(
        x0_m=float(x0 + xc),
        t0_ns=float(t0),
        velocity_m_per_ns=float(v),
        n_picks=int(x.size),
        rms_residual_ns=float(np.sqrt(np.mean(resid**2))),
        r2=float(r2),
    )


def _scan(env, j0, t0, dt_ns, dx_m, half_ap, velocities, shifts):
    """Best (score, x-shift in traces, velocity): mean envelope along trial hyperbolas near the candidate apex."""
    n, m = env.shape
    best = (-np.inf, 0, velocities[0])
    for s in shifts:
        jc = j0 + s
        js = np.arange(max(0, jc - half_ap), min(m, jc + half_ap + 1))
        if js.size == 0:
            continue
        dx2 = ((js - jc) * dx_m) ** 2
        t = np.sqrt(t0**2 + 4.0 * dx2[None, :] / velocities[:, None] ** 2)
        ii = np.rint(t / dt_ns).astype(int)
        ok = ii < n
        vals = np.where(ok, env[np.minimum(ii, n - 1), js[None, :]], 0.0)
        scores = vals.sum(axis=1) / js.size
        k = int(np.argmax(scores))
        if scores[k] > best[0]:
            best = (float(scores[k]), int(s), float(velocities[k]))
    return best


def pick_hyperbola(
    env: np.ndarray,
    x0_m: float,
    t0_ns: float,
    v: float,
    dt_ns: float,
    dx_m: float,
    half_ap: int,
    search_ns: float,
    min_rel_amp: float = 0.2,
) -> tuple[np.ndarray, np.ndarray]:
    """Envelope-peak picks within +-search_ns of the trial hyperbola; skips traces where it has faded."""
    n, m = env.shape
    w = max(int(round(search_ns / dt_ns)), 1)
    j0 = int(round(x0_m / dx_m))
    i_apex = min(max(int(round(t0_ns / dt_ns)), 0), n - 1)
    apex_amp = env[max(i_apex - w, 0) : i_apex + w + 1, min(max(j0, 0), m - 1)].max()
    xs, ts = [], []
    for j in range(max(0, j0 - half_ap), min(m, j0 + half_ap + 1)):
        dx = j * dx_m - x0_m
        ic = int(round(np.sqrt(t0_ns**2 + 4.0 * dx**2 / v**2) / dt_ns))
        lo, hi = max(0, ic - w), min(n, ic + w + 1)
        if hi - lo < 3:
            continue
        seg = env[lo:hi, j]
        k = int(np.argmax(seg))
        if seg[k] < min_rel_amp * apex_amp or k in (0, seg.size - 1):
            continue  # faded, or the peak is outside the window
        den = seg[k - 1] - 2 * seg[k] + seg[k + 1]
        frac = 0.5 * (seg[k - 1] - seg[k + 1]) / den if den != 0 else 0.0
        xs.append(j * dx_m)
        ts.append((lo + k + frac) * dt_ns)
    return np.array(xs), np.array(ts)


def refine_hyperbola(
    env: np.ndarray, x0_m: float, t0_ns: float, v: float, dt_ns: float, dx_m: float, half_ap: int,
    search_ns: float, iterations: int = 3,
) -> tuple[HyperbolaFit, np.ndarray, np.ndarray]:
    """Pick -> fit -> re-pick along the fitted curve, dropping outlier picks.

    Returns (fit, x picks, t picks). Raises ValueError on failure.
    """
    for _ in range(iterations):
        xs, ts = pick_hyperbola(env, x0_m, t0_ns, v, dt_ns, dx_m, half_ap, search_ns)
        fit = fit_hyperbola(xs, ts)
        model = np.sqrt(fit.t0_ns**2 + 4.0 * (xs - fit.x0_m) ** 2 / fit.velocity_m_per_ns**2)
        r = np.abs(ts - model)
        keep = r <= max(3.0 * np.median(r), 0.5 * dt_ns)
        if keep.sum() >= 5 and keep.sum() < xs.size:
            xs, ts = xs[keep], ts[keep]
            fit = fit_hyperbola(xs, ts)
        if not (V_MIN <= fit.velocity_m_per_ns <= V_MAX):
            raise ValueError("velocity outside physical range")
        x0_m, t0_ns, v = fit.x0_m, fit.t0_ns, fit.velocity_m_per_ns
    return fit, xs, ts


def find_apex_candidates(
    env: np.ndarray, dt_ns: float, dx_m: float, max_candidates: int = 30, min_t_ns: float = 2.0
) -> list[tuple[int, int]]:
    """Strong local envelope maxima (sample, trace), strongest first.

    Envelope is divided by each trace's median level first, so full-depth vertical
    stripes (antenna lift, noise bursts) do not crowd out real diffractions.
    """
    n, m = env.shape
    trace_level = np.median(env, axis=0, keepdims=True)
    score = env / (trace_level + np.median(env) + 1e-20)
    size_t = max(int(round(10.0 / dt_ns)), 3)
    size_x = max(int(round(1.0 / dx_m)), 5)
    mx = ndimage.maximum_filter(score, size=(size_t, size_x), mode="nearest")
    thr = np.percentile(score, 99.0)
    i_min = int(np.ceil(min_t_ns / dt_ns))
    peaks = np.argwhere((score == mx) & (score > thr))
    peaks = peaks[peaks[:, 0] >= i_min]
    order = np.argsort(-score[peaks[:, 0], peaks[:, 1]])
    return [tuple(map(int, p)) for p in peaks[order][:max_candidates]]


def _overlap(xs: np.ndarray, ts: np.ndarray, g: HyperbolaFit, tol_ns: float) -> float:
    """Fraction of picks lying on hyperbola g."""
    tg = np.sqrt(g.t0_ns**2 + 4.0 * (xs - g.x0_m) ** 2 / g.velocity_m_per_ns**2)
    return float(np.mean(np.abs(ts - tg) <= tol_ns))


def estimate_velocity(
    data: np.ndarray,
    dt_ns: float,
    dx_m: float,
    freq_mhz: float,
    max_candidates: int = 30,
    aperture_m: float = 2.0,
    min_picks: int = 15,
    min_r2: float = 0.9,
    min_contrast: float = 4.0,
) -> tuple[float | None, list[HyperbolaFit]]:
    """Velocity from all hyperbolas found in a background-removed section.

    Returns (median velocity of accepted fits or None, accepted fits).
    Each candidate apex: velocity scan -> picks near best curve -> x^2-t^2 fit,
    accepted only if the curve is well sampled, fits tightly and is physical.
    """
    env = envelope(data).astype(np.float64)
    period_ns = 1e3 / freq_mhz
    velocities = np.linspace(V_MIN, V_MAX, 91)
    background = float(np.median(env)) + 1e-20
    fits: list[HyperbolaFit] = []
    shifts = np.arange(-int(round(0.5 / dx_m)), int(round(0.5 / dx_m)) + 1, max(int(round(0.05 / dx_m)), 1))
    for i0, j0 in find_apex_candidates(env, dt_ns, dx_m, max_candidates):
        t0 = i0 * dt_ns
        # Aperture: the cone a slow-to-medium velocity hyperbola spans, at least aperture_m.
        half_ap = int(round(max(aperture_m, 0.5 * V_MAX * t0 / 2.0) / dx_m))
        _, shift, v_best = _scan(env, j0, t0, dt_ns, dx_m, half_ap, velocities, shifts)
        if v_best >= V_MAX * 0.99:
            continue  # flat event: layer remnant, not a diffraction
        try:
            f, xs, ts = refine_hyperbola(env, (j0 + shift) * dx_m, t0, v_best, dt_ns, dx_m, half_ap, 0.5 * period_ns)
        except ValueError:
            continue
        # Random picks inside the +-half-period search window scatter by ~0.3 period,
        # a real hyperbola by a few percent of it.
        if f.n_picks < min_picks or f.r2 < min_r2 or f.rms_residual_ns > 0.1 * period_ns:
            continue
        # The event must stand out from the section, not be the best path through noise.
        on_curve = env[np.clip(np.rint(ts / dt_ns).astype(int), 0, env.shape[0] - 1), np.rint(xs / dx_m).astype(int)]
        if np.median(on_curve) < min_contrast * background:
            continue
        # Both limbs must be sampled: a single limb (or a dipping layer crossing one) fits any velocity.
        left, right = np.sum(xs < f.x0_m - dx_m), np.sum(xs > f.x0_m + dx_m)
        if min(left, right) < 0.3 * max(left, right):
            continue
        # Mostly the same picks as a stronger accepted hyperbola: a duplicate or a flank crossing.
        if any(_overlap(xs, ts, g, 0.5 * period_ns) > 0.5 for g in fits):
            continue
        fits.append(f)
    if not fits:
        return None, []
    return float(np.median([f.velocity_m_per_ns for f in fits])), fits
