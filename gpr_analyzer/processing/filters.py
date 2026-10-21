"""Individual processing steps. Each works on a (samples, traces) array and returns a new one.

Parameter choice lives in auto.py; these functions only apply what they are given.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage, signal


def mean_spectrum(data: np.ndarray, dt_ns: float) -> tuple[np.ndarray, np.ndarray]:
    """Mean amplitude spectrum over traces. Returns (freq_mhz, amplitude)."""
    n = data.shape[0]
    taper = signal.windows.tukey(n, 0.1)[:, None]
    spec = np.abs(np.fft.rfft((data - data.mean(axis=0)) * taper, axis=0)).mean(axis=1)
    freq_mhz = np.fft.rfftfreq(n, d=dt_ns * 1e-9) / 1e6
    return freq_mhz, spec


def estimate_center_frequency(data: np.ndarray, dt_ns: float, min_mhz: float = 20.0) -> float:
    """Dominant frequency (MHz) of the data, ignoring wow below min_mhz.

    Uses the amplitude-weighted centroid around the spectral peak, which is
    steadier than the raw argmax on short traces.
    """
    f, a = mean_spectrum(data, dt_ns)
    keep = f >= min_mhz
    f, a = f[keep], a[keep]
    if a.size == 0 or not np.any(a > 0):
        raise ValueError("cannot estimate frequency: empty spectrum")
    a = ndimage.uniform_filter1d(a, 3)
    peak = int(np.argmax(a))
    band = a >= 0.5 * a[peak]
    # contiguous half-power lobe around the peak
    lo = peak
    while lo > 0 and band[lo - 1]:
        lo -= 1
    hi = peak
    while hi < a.size - 1 and band[hi + 1]:
        hi += 1
    w = a[lo : hi + 1]
    return float(np.sum(f[lo : hi + 1] * w) / np.sum(w))


def dewow(data: np.ndarray, dt_ns: float, window_ns: float) -> np.ndarray:
    """Remove the low-frequency 'wow' by subtracting a Gaussian-smoothed copy of each trace.

    A Gaussian (not a boxcar) is used so the antenna band is left intact:
    with sigma = window_ns / 2 the smoothing passes almost nothing near the antenna frequency.
    """
    sigma = max(window_ns / 2.0 / dt_ns, 1.0)
    low = ndimage.gaussian_filter1d(data.astype(np.float64), sigma, axis=0, mode="nearest")
    return (data - low).astype(np.float32)


def first_break_picks(data: np.ndarray, threshold: float = 0.3, search_samples: int = 10) -> np.ndarray:
    """Direct-wave peak on each trace, in fractional samples.

    The first sample whose |amplitude| reaches threshold x the trace's max starts
    the search; the pick is the largest |amplitude| within search_samples after it
    (about one period, so a wavelet side lobe is not mistaken for the main peak),
    refined to sub-sample precision with a parabola through the peak.
    """
    a = np.abs(data).astype(np.float64)
    mx = a.max(axis=0)
    mx[mx == 0] = 1.0
    first = np.argmax(a >= threshold * mx, axis=0)
    n = data.shape[0]
    picks = np.empty(first.size)
    for j, i in enumerate(first):
        hi = min(n, i + search_samples + 1)
        k = i + int(np.argmax(a[i:hi, j]))
        frac = 0.0
        if 0 < k < n - 1:
            den = a[k - 1, j] - 2 * a[k, j] + a[k + 1, j]
            if den != 0:
                frac = float(np.clip(0.5 * (a[k - 1, j] - a[k + 1, j]) / den, -0.5, 0.5))
        picks[j] = k + frac
    return picks


def time_zero_correct(data: np.ndarray, picks: np.ndarray, target_sample: int) -> np.ndarray:
    """Shift each trace (sub-sample, cubic spline) so its pick lands on target_sample, then cut above it.

    Output row 0 is time zero (the direct-wave peak, i.e. the ground surface for a
    ground-coupled antenna). Samples shifted in from outside are zero. Sub-sample
    shifts matter: a half-sample misalignment leaves a checkered residue in
    horizontal bands after background removal.
    """
    n, m = data.shape
    rows = np.arange(target_sample, n, dtype=np.float64)[:, None] + (np.asarray(picks, float) - target_sample)[None, :]
    cols = np.broadcast_to(np.arange(m, dtype=np.float64)[None, :], rows.shape)
    out = ndimage.map_coordinates(data.astype(np.float64), [rows, cols], order=3, mode="constant", cval=0.0)
    return out.astype(np.float32)


def bandpass(data: np.ndarray, dt_ns: float, low_mhz: float, high_mhz: float, order: int = 4) -> np.ndarray:
    """Zero-phase Butterworth bandpass along time."""
    fs_mhz = 1e3 / dt_ns
    nyq = fs_mhz / 2.0
    high = min(high_mhz, 0.95 * nyq)
    low = max(low_mhz, 1e-3)
    if low >= high:
        raise ValueError(f"invalid band {low_mhz}-{high_mhz} MHz (Nyquist {nyq:.0f} MHz)")
    sos = signal.butter(order, [low, high], btype="bandpass", fs=fs_mhz, output="sos")
    return signal.sosfiltfilt(sos, data.astype(np.float64), axis=0).astype(np.float32)


def background_removal(data: np.ndarray, window_traces: int | None = None, statistic: str = "median") -> np.ndarray:
    """Subtract the background trace: global (window_traces=None) or a sliding window over traces.

    Removes horizontal bands (antenna ringing, direct-wave residue). statistic="median"
    ignores events that cover less than half the window (hyperbola apexes), so it
    does not paint flat negative ghosts of them the way a mean does.
    """
    d = data.astype(np.float64)
    m = d.shape[1]
    if statistic not in ("mean", "median"):
        raise ValueError(f"unknown statistic {statistic!r}")
    if window_traces is None or window_traces >= m:
        bg = np.median(d, axis=1, keepdims=True) if statistic == "median" else d.mean(axis=1, keepdims=True)
    else:
        w = max(int(window_traces) | 1, 3)
        if statistic == "median":
            bg = _sliding_median(d, w)
        else:
            bg = ndimage.uniform_filter1d(d, w, axis=1, mode="reflect")
    return (d - bg).astype(np.float32)


def _sliding_median(d: np.ndarray, w: int) -> np.ndarray:
    """Median over a w-trace window, evaluated every ~w/10 traces and interpolated between.

    The background changes slowly along the line, so this matches a full sliding
    median closely at a fraction of the cost.
    """
    m = d.shape[1]
    step = max(w // 10, 1)
    centres = np.unique(np.r_[np.arange(0, m, step), m - 1])
    half = w // 2
    meds = np.empty((d.shape[0], centres.size))
    for k, c in enumerate(centres):
        lo, hi = max(0, c - half), min(m, c + half + 1)
        meds[:, k] = np.median(d[:, lo:hi], axis=1)
    if centres.size == 1:
        return np.repeat(meds, m, axis=1)
    cols = np.arange(m)
    idx = np.clip(np.searchsorted(centres, cols, side="right") - 1, 0, centres.size - 2)
    c0, c1 = centres[idx], centres[idx + 1]
    frac = (cols - c0) / np.maximum(c1 - c0, 1)
    return meds[:, idx] * (1 - frac) + meds[:, idx + 1] * frac


def envelope(data: np.ndarray) -> np.ndarray:
    """Instantaneous amplitude (Hilbert envelope) along time."""
    return np.abs(signal.hilbert(data.astype(np.float64), axis=0)).astype(np.float32)


def energy(data: np.ndarray, dt_ns: float, window_ns: float) -> np.ndarray:
    """Moving-window RMS energy along time."""
    w = max(int(round(window_ns / dt_ns)), 1)
    e = ndimage.uniform_filter1d(data.astype(np.float64) ** 2, w, axis=0, mode="nearest")
    return np.sqrt(e).astype(np.float32)


def amplitude_decay(data: np.ndarray, dt_ns: float, smooth_ns: float = 10.0, percentile: float = 90.0) -> np.ndarray:
    """Envelope level against time (percentile over traces, smoothed): the curve SEC gain compensates.

    A high percentile rather than the mean, so a few artifact traces do not set the
    level and sparse sections still show where real reflections fade into noise.
    """
    env = np.percentile(envelope(data).astype(np.float64), percentile, axis=1)
    sigma = max(smooth_ns / dt_ns, 1.0)
    return ndimage.gaussian_filter1d(env, sigma, mode="nearest")


def estimate_noise_floor(decay: np.ndarray, tail_fraction: float = 0.15) -> float:
    """Noise level: the median of the decay curve over the last tail_fraction of the window."""
    n = decay.size
    tail = decay[int(n * (1 - tail_fraction)) :]
    return float(np.median(tail)) if tail.size else float(decay[-1])


def sec_gain_curve(
    decay: np.ndarray,
    dt_ns: float,
    noise_floor: float,
    floor_factor: float = 2.0,
    max_gain_db: float = 60.0,
) -> tuple[np.ndarray, int, float]:
    """Exponential SEC gain fitted to the observed decay, held constant from the noise floor down.

    The decay rate is a least-squares fit of log(level) against time between the
    level's peak and the last sample still above floor_factor x noise floor (the
    cap). Below the cap the gain stops growing, so noise at the bottom of the
    section (the red band at 120-140 ns in the SubScan sample) is not boosted.
    Returns (gain per sample, cap sample, decay rate in dB/ns).
    """
    n = decay.size
    level = noise_floor * floor_factor
    start = int(np.argmax(decay))
    above = np.nonzero(decay > level)[0]
    above = above[above >= start]
    cap = int(above[-1]) if above.size else start
    rate_db = 0.0
    if cap - start >= 3:
        t = np.arange(start, cap + 1) * dt_ns
        slope = np.polyfit(t, 20.0 * np.log10(np.maximum(decay[start : cap + 1], 1e-20)), 1)[0]
        rate_db = max(-slope, 0.0)
    t_all = (np.arange(n) - start).clip(0, cap - start) * dt_ns
    g_db = np.minimum(rate_db * t_all, max_gain_db)
    return 10 ** (g_db / 20.0), cap, float(rate_db)


def apply_gain(data: np.ndarray, gain: np.ndarray) -> np.ndarray:
    return (data * gain[:, None]).astype(np.float32)


def agc(data: np.ndarray, dt_ns: float, window_ns: float, noise_rms: float = 0.0) -> np.ndarray:
    """Automatic gain control: divide by the moving RMS. Display only: it destroys relative amplitude.

    noise_rms stabilises the division (sqrt(rms^2 + noise^2)) so stretches of pure
    noise are not stretched to full scale.
    """
    rms = energy(data, dt_ns, window_ns).astype(np.float64)
    eps = 1e-6 * (rms.max() if rms.size else 1.0) + 1e-20
    return (data / np.sqrt(rms**2 + noise_rms**2 + eps**2)).astype(np.float32)


def noise_rms(data: np.ndarray, dt_ns: float, window_ns: float, tail_fraction: float = 0.15) -> float:
    """Typical RMS level of the last tail_fraction of the time window (median over traces)."""
    n = data.shape[0]
    tail = data[int(n * (1 - tail_fraction)) :]
    if tail.size == 0:
        return 0.0
    return float(np.median(energy(tail, dt_ns, window_ns)))
