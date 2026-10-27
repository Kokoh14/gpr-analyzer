"""Automatic display filters: each parameter is chosen from measurements on the line itself.

Display only. The chain starts from a copy of the time-zeroed, dewowed data that the analysis
track also starts from, and nothing here feeds back into the analysis track, detection or scoring.

Choices, all logged with the measurements behind them:
- band-pass: the frequency range where the signal part of the section is at least 6 dB above
  the noise-only part (below the depth where reflections fade into noise), never narrower than
  the signal's own half-power band, so the wavelet is not made to ring.
- background removal: sliding median; the window is the widest candidate that leaves under 5 %
  banding along most of the line at the times where every trace rings alike (direct wave,
  antenna ringing). Wide windows keep flat reflectors (layers, cavity roofs), so the widest one
  that does the job is taken. Ringing that changes along the line (e.g. where the surface
  changes) needs a shorter window.
- gain: one curve for the whole line that flattens the measured reflection-strength decay
  down to where it meets the noise floor, constant below. Unlike AGC every trace gets the same
  gain, so strengths at the same depth stay comparable along the line.
- contrast: the colour scale clips at the 99th percentile of everything below the direct wave.

Dead traces (all zero) are left out of the background statistics and stay blank.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import signal

from gpr_analyzer.processing import filters as F

SNR_DB = 6.0  # pass band: signal power at least this far above noise power
RESIDUAL_MAX = 0.05  # ringing left after removal, as a share of the energy at ringing times
MIN_WINDOW_M = 2.0  # narrower windows start to flatten hyperbola apexes (first Fresnel zone ~0.5-1 m)
WINDOWS_M = (30.0, 20.0, 15.0, 10.0, 7.5, 5.0, 3.0, 2.0)  # tried widest first, after "whole line"
BLOCK_M = 2.0  # banding is measured on medians over blocks this long (ignores hyperbolas)
CLIP_PCT = 99.0


def _psd(seg: np.ndarray, dt_ns: float, n_fft: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean power spectral density over traces, per unit length so short and long segments compare."""
    taper = signal.windows.tukey(seg.shape[0], 0.2)[:, None]
    x = (seg - seg.mean(axis=0)) * taper
    p = (np.abs(np.fft.rfft(x, n=n_fft, axis=0)) ** 2).mean(axis=1) / float(np.sum(taper**2))
    return np.fft.rfftfreq(n_fft, d=dt_ns * 1e-9) / 1e6, p


def _lobe(mask: np.ndarray, peak: int) -> tuple[int, int]:
    lo = hi = peak
    while lo > 0 and mask[lo - 1]:
        lo -= 1
    while hi < mask.size - 1 and mask[hi + 1]:
        hi += 1
    return lo, hi


def choose_band(d: np.ndarray, dt_ns: float, fc: float, cap: int, skip: int) -> tuple[float, float, dict[str, Any], str]:
    """Pass band from the signal-to-noise spectrum. Rows skip..cap are signal, rows after cap noise only."""
    nyq = 0.5e3 / dt_ns
    n_noise = d.shape[0] - cap - 1
    min_rows = max(32, int(round(4e3 / fc / dt_ns)))  # at least 4 periods of noise
    if cap - skip < min_rows or n_noise < min_rows:
        lo, hi = 0.4 * fc, min(2.0 * fc, 0.95 * nyq)
        return lo, hi, {"method": "fixed 0.4x-2x centre frequency"}, (
            "no noise-only part below the reflections to measure noise against, so the standard antenna band is used")
    n_fft = int(2 ** np.ceil(np.log2(max(cap - skip, n_noise) * 2)))
    f, ps = _psd(d[skip : cap + 1], dt_ns, n_fft)
    _, pn = _psd(d[cap + 1 :], dt_ns, n_fft)
    # ps holds signal + noise; the excess over the noise is the signal
    snr = (ps - pn) / np.maximum(pn, 1e-30)
    use = (f >= 0.3 * fc) & (f <= 0.95 * nyq)  # below 0.3 fc only wow and drift are left
    peak = int(np.argmax(np.where(use, ps, 0.0)))
    lo_i, hi_i = _lobe(use & (snr >= 10 ** (SNR_DB / 10)), peak)
    half_lo, half_hi = _lobe(use & (ps >= 0.5 * ps[peak]), peak)
    lo_i, hi_i = min(lo_i, half_lo), max(hi_i, half_hi)
    lo, hi = float(f[lo_i]), min(float(f[hi_i]), 0.95 * nyq)
    params = {"method": f"signal >= {SNR_DB:g} dB above noise",
              "peak_snr_db": round(float(10 * np.log10(max(snr[peak], 1e-12))), 1),
              "signal_window_ns": [round(skip * dt_ns, 1), round(cap * dt_ns, 1)]}
    why = ("frequencies where the reflections stand clear of the noise measured below them; "
           "never narrower than the signal's half-power band so the wavelet does not ring")
    if (lo_i, hi_i) == (half_lo, half_hi):
        why = "signal barely above noise: kept to the signal's half-power band (narrower would ring)"
    return lo, hi, params, why


def banding(d: np.ndarray, block: int, rows: np.ndarray | slice) -> float:
    """Horizontal energy left along most of the line, as a share of the energy in the given rows.

    Per row: the median over blocks of traces of (block median)^2, divided by the row's mean
    energy; then averaged over rows. Hyperbolas, noise and anything narrower than half a block drop
    out of a block median, so only horizontal events count. Taking the median over blocks means a
    flat event must cover most of the line to count: ringing does, a cavity roof or a local layer
    does not, so those are not mistaken for ringing that a shorter window should remove.
    """
    x = d[rows].astype(np.float64)
    m = x.shape[1]
    nb = max(m // block, 1)
    edges = [k * block for k in range(nb)] + [m]
    meds = np.stack([np.median(x[:, a:b], axis=1) for a, b in zip(edges, edges[1:])], axis=1)
    band_e = np.median(meds**2, axis=1)
    tot = (x**2).mean(axis=1)
    ok = tot > 0
    return float(np.mean(band_e[ok] / tot[ok])) if ok.any() else 0.0


def system_rows(d: np.ndarray, rows: slice) -> np.ndarray:
    """Rows (within rows) where the line's median trace carries at least half the energy.

    These are the times of the direct wave and antenna ringing, which sit at the same time on every
    trace. A dipping or local reflector covers too few traces at any one time to qualify, so the
    window choice below is not driven by real geology.
    """
    x = d[rows].astype(np.float64)
    bg = np.median(x, axis=1)
    tot = (x**2).sum(axis=1)
    share = np.where(tot > 0, bg**2 * x.shape[1] / np.maximum(tot, 1e-30), 0.0)
    return np.arange(x.shape[0])[share >= 0.5] + (rows.start or 0)


def _remove_background(d: np.ndarray, window: int | None, live: np.ndarray) -> np.ndarray:
    out = np.zeros_like(d)
    out[:, live] = F.background_removal(d[:, live], window, "median")
    return out


def choose_background(d: np.ndarray, dt_ns: float, dx_m: float | None, rows: slice, live: np.ndarray) -> tuple[np.ndarray, dict[str, Any], str]:
    """Widest sliding-median window that removes most of the banding (see module docstring)."""
    m = int(live.sum())
    if dx_m is None or m < 8:
        out = _remove_background(d, None, live)
        why = "no trace spacing in the file" if dx_m is None else "too few traces to compare windows"
        return out, {"window": "whole line", "statistic": "median"}, why + ", so one background for the whole line"
    block = max(int(round(BLOCK_M / dx_m)), 3)
    lv = d[:, live]
    sys_rows = system_rows(lv, rows)
    if sys_rows.size == 0:
        return d, {"applied": False}, "no horizontal banding at a fixed time (ringing) found, so nothing is removed and flat reflectors stay"
    before = banding(lv, block, sys_rows)
    cands: list[tuple[str, int | None]] = [("whole line", None)]
    for w in WINDOWS_M:
        n = _odd(w / dx_m)
        if n < m and w >= MIN_WINDOW_M:
            cands.append((f"{w:g} m", n))
    # background removal works row by row, so candidates are scored on the ringing rows only
    ring = lv[sys_rows]
    every = slice(None)
    scores = [banding(F.background_removal(ring, n, "median"), block, every) for _, n in cands]
    best = min(scores)
    need = max(RESIDUAL_MAX, 2.0 * best)
    k = next(i for i, sc in enumerate(scores) if sc <= need + 1e-12)
    (label, n), after = cands[k], scores[k]
    out = _remove_background(d, n, live)
    params = {"window": label, "window_traces": n if n else "all", "statistic": "median",
              "banding_before_pct": round(100 * before, 1), "banding_after_pct": round(100 * after, 1),
              "narrowest_tried_pct": round(100 * best, 1), "ringing_ns": _spans(sys_rows * dt_ns)}
    why = (f"widest window that leaves a banding share under {RESIDUAL_MAX:g} (or twice what the narrowest, {cands[-1][0]}, leaves), "
           "measured only at the times where every trace rings alike; wider windows keep flat reflectors such as cavity roofs")
    return out, params, why


def _spans(t: np.ndarray) -> str:
    """Evenly spaced times as compact ranges, e.g. "0-3.1, 35.2-38.4"."""
    if t.size < 2:
        return ", ".join(f"{x:.1f}" for x in t)
    cut = np.nonzero(np.diff(t) > 1.5 * np.min(np.diff(t)))[0]
    starts, ends = np.r_[t[0], t[cut + 1]], np.r_[t[cut], t[-1]]
    return ", ".join(f"{a:.1f}-{b:.1f}" for a, b in zip(starts, ends))


def _odd(n: float) -> int:
    k = max(int(round(n)), 3)
    return k if k % 2 else k + 1


def display_gain(d: np.ndarray, dt_ns: float, max_gain_db: float) -> tuple[np.ndarray, int, dict[str, Any]]:
    """Inverse of the measured decay down to 2x the noise floor, one curve for every trace.

    The decay is made non-increasing first, so the gain never drops with depth: a strong deep
    layer is shown strong instead of being turned down to match its surroundings.
    """
    # zero padding: the FFT Hilbert envelope wraps the strong direct wave onto the last samples
    n = d.shape[0]
    decay = F.amplitude_decay(np.vstack([d, np.zeros((n, d.shape[1]), d.dtype)]), dt_ns)[:n]
    nf = F.estimate_noise_floor(decay)
    start = int(np.argmax(decay))
    above = np.nonzero(decay > 2.0 * nf)[0]
    above = above[above >= start]
    cap = int(above[-1]) if above.size else start
    level = np.minimum.accumulate(decay[start : cap + 1])
    ref = level[np.clip(np.arange(n), start, cap) - start]
    g = np.minimum(decay[start] / np.maximum(ref, 1e-30), 10 ** (max_gain_db / 20.0))
    params = {"max_gain_db": round(float(20 * np.log10(g.max())), 1), "flat_below_ns": round(cap * dt_ns, 1)}
    return g.astype(np.float32), cap, params


def auto_display(d0: np.ndarray, dt_ns: float, dx_m: float | None, fc: float,
                 max_gain_db: float = 60.0) -> tuple[np.ndarray, list[tuple[str, dict[str, Any], str]], float]:
    """Run the automatic display chain on time-zeroed, dewowed data (samples, traces).

    Returns (display array, [(step, params, reason)], colour clip value). d0 is not modified.
    """
    steps: list[tuple[str, dict[str, Any], str]] = []
    d = np.array(d0, dtype=np.float32, copy=True)
    live = np.ptp(d, axis=0) > 0
    if not live.any():
        return d, [("display_qc", {"dead_traces": int(d.shape[1])}, "no live traces")], 1.0
    if not live.all():
        steps.append(("display_qc", {"dead_traces": int((~live).sum())},
                      "all-zero traces left out of the background statistics and shown blank"))

    # Where the reflections end: decay of the raw section against its own noise floor.
    _, cap, _ = display_gain(d[:, live], dt_ns, max_gain_db)
    skip = min(int(round(2e3 / fc / dt_ns)), cap)  # past the direct wave
    lo, hi, bp, why = choose_band(d[:, live], dt_ns, fc, cap, skip)
    d[:, live] = F.bandpass(d[:, live], dt_ns, lo, hi)
    steps.append(("display_bandpass", {"low_mhz": round(lo, 1), "high_mhz": round(hi, 1), **bp}, why))

    d, bg, why = choose_background(d, dt_ns, dx_m, slice(0, max(cap + 1, 2)), live)
    steps.append(("display_background", bg, why))

    g, cap2, gp = display_gain(d[:, live], dt_ns, max_gain_db)
    d = F.apply_gain(d, g)
    steps.append(("display_gain", gp,
                  "flattens the measured reflection-strength decay down to the noise floor; one curve for the whole line, "
                  "so strengths at the same depth stay comparable (AGC does not keep this)" if cap2 > 0 else
                  "reflections do not stand above the noise anywhere below the direct wave, so no gain is applied"))

    sig = np.abs(d[skip:][:, live])  # past the direct wave; noise below cap2 is not boosted, so it sits low
    clip = float(np.percentile(sig, CLIP_PCT)) or 1.0
    steps.append(("display_contrast", {"clip_percentile": CLIP_PCT, "clip_value": float(f"{clip:.4g}")},
                  "colour scale saturates only the strongest 1 in 100 samples below the direct wave"))
    return d, steps, clip


__all__ = ["auto_display", "choose_band", "choose_background", "banding", "display_gain"]
