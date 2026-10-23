"""Display chain fitted to ImpulseRadar ViewPoint screenshots (clean-room, outside-in).

Fitted only from the user's raw CrossOver 4080 files and the screenshots / field PDF that
ViewPoint exported for them (scripts/fit_viewpoint_screens.py, docs/viewpoint-fit.md).
No vendor code was read.

What the screen shows, as measured:
- depth 0 at the header's ZERO LEVEL sample, depth = sqrt((v t/2 + S/2)^2 - (S/2)^2) with
  v = SOIL VELOCITY and S = ANTENNA SEPARATION (core.depth_from_time). Depth ticks on 18
  screenshots land within 0.5 sample of this; 4 of the 6 marker depths in the field PDF match
  to the printed centimetre, the other 2 are 1 cm off.
- background removal: running mean over about 1.9 m (101 traces at 0.0186 m); this beat
  global mean/median and windows of 11-401 traces on every screenshot.
- dewow: barely matters (any 1.5-3 periods scores the same).
- gain: grows with time and flattens: dB(t) = G (1 - exp(-t / tau)), t from time zero.
  800 MHz: G 58 dB, tau 20 ns; 400 MHz: G 32 dB, tau 16 ns (row-by-row fit, RMSE 1.4 / 1.8 dB).
  A pure exponential misfits by 3.7-3.9 dB. Screen contrast in ViewPoint is user-adjustable,
  so these are the settings of this survey, used as defaults.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.processing import filters as F

BACKGROUND_WINDOW_M = 1.9
BACKGROUND_WINDOW_TRACES = 101  # when the file has no trace spacing
# nominal antenna MHz -> (G dB, tau ns)
GAIN_TABLE = {800.0: (58.0, 20.2), 400.0: (32.3, 16.3)}


def _odd(n: float) -> int:
    k = max(int(round(n)), 3)
    return k if k % 2 else k + 1


def display_gain_curve(n_samples: int, dt_ns: float, freq_mhz: float | None) -> tuple[np.ndarray, dict[str, Any]]:
    """Gain per sample (linear factor) for a time-zeroed trace, and the parameters used."""
    nominal = min(GAIN_TABLE, key=lambda f: abs(np.log(f / freq_mhz))) if freq_mhz else 400.0
    g_db, tau = GAIN_TABLE[nominal]
    t = np.arange(n_samples) * dt_ns
    db = g_db * (1.0 - np.exp(-t / tau))
    return (10.0 ** (db / 20.0)).astype(np.float32), {"max_gain_db": g_db, "tau_ns": tau, "fitted_on_mhz": nominal}


def viewpoint_display(rg: Radargram, zero_sample: int | None = None) -> tuple[Radargram, dict[str, Any]]:
    """Reproduce the ViewPoint screen for one channel. Returns (radargram, parameters).

    zero_sample: time-zero sample; default the header ZERO LEVEL, else no cut.
    Output row 0 is time zero; amplitudes are display-only (do not score on them).
    """
    d = rg.data.astype(np.float64)
    d = d - d.mean(axis=0, keepdims=True)
    f = rg.metadata.get("antenna_frequency_mhz")
    fc = float(f) if f else F.estimate_center_frequency(d, rg.dt_ns)
    d = F.dewow(d, rg.dt_ns, 2.0 * 1e3 / fc)
    z = zero_sample if zero_sample is not None else rg.metadata.get("zero_level_sample")
    if z:
        d = F.time_zero_static(d, int(z))
    if rg.dx_m:
        w = _odd(BACKGROUND_WINDOW_M / rg.dx_m)
    else:
        w = BACKGROUND_WINDOW_TRACES
    if w < d.shape[1]:
        d = F.background_removal(d, w, "mean")
    else:
        d = F.background_removal(d, None, "mean")
    gain, gp = display_gain_curve(d.shape[0], rg.dt_ns, fc)
    d = F.apply_gain(d, gain)
    params = {
        "time_zero_sample": int(z) if z else 0,
        "dewow_window_ns": round(2.0 * 1e3 / fc, 2),
        "background_window_traces": int(min(w, d.shape[1])),
        "background_statistic": "mean",
        **gp,
    }
    out = rg.copy_with(data=d.astype(np.float32))
    out.metadata["track"] = "viewpoint"
    return out, params


__all__ = ["viewpoint_display", "display_gain_curve", "GAIN_TABLE", "BACKGROUND_WINDOW_M"]
