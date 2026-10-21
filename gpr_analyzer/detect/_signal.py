"""Small signal helpers shared by the detectors (numpy / scipy only)."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter, uniform_filter, uniform_filter1d
from scipy.signal import hilbert


def remove_background(data: np.ndarray) -> np.ndarray:
    """Subtract the per-sample median trace: flattens the direct wave and horizontal ringing."""
    return data - np.median(data, axis=1, keepdims=True)


def envelope(data: np.ndarray) -> np.ndarray:
    """Instantaneous amplitude along time (Hilbert transform per trace)."""
    return np.abs(hilbert(data, axis=0))


def dominant_period_samples(data: np.ndarray, lo: int = 3, hi: int = 200) -> int:
    """Period of the strongest frequency in the mean amplitude spectrum, in samples."""
    n = data.shape[0]
    spec = np.abs(np.fft.rfft(data - data.mean(axis=0), axis=0)).mean(axis=1)
    freqs = np.fft.rfftfreq(n)
    spec[0] = 0.0
    k = int(np.argmax(spec))
    if k == 0 or freqs[k] == 0:
        return 10
    return int(np.clip(round(1.0 / freqs[k]), lo, hi))


def depth_contrast(env: np.ndarray, period: int) -> np.ndarray:
    """Envelope divided by the background envelope level at the same time.

    The background is the median across traces, then a running median over ~8 periods of time,
    so a single long layer (one period thick) does not lift its own reference. Works on gained
    and ungained data alike: a reflector at depth is compared with the background at that depth,
    not with the bright top of the section.
    """
    row = np.median(env, axis=1)
    row = median_filter(row, size=max(5, 8 * period), mode="nearest")
    floor = max(float(np.median(env)) * 1e-3, 1e-12)
    return env / np.maximum(row, floor)[:, None]


def direct_wave_end(data: np.ndarray, period: int) -> int:
    """First sample below the direct (air/ground) wave, or ~one period when there is none.

    The direct wave is the strong, laterally uniform event in the first few periods of a
    time-zero-corrected record. If background removal already took it out, nothing is skipped.
    """
    n = data.shape[0]
    env = envelope(data)
    row = np.median(env, axis=1)
    search = min(n, max(3 * period, n // 10))
    k = int(np.argmax(row[:search]))
    if row[k] >= 5.0 * max(float(np.median(row)), 1e-12):
        return min(n - 1, k + 2 * period)
    return min(n - 1, period)


def contrast_section(data: np.ndarray) -> tuple[np.ndarray, int, int]:
    """(depth-normalised envelope, dominant period in samples, first sample below the direct wave)."""
    d = np.asarray(data, dtype=np.float64)
    per = dominant_period_samples(d)
    c = depth_contrast(envelope(d), per)
    c = uniform_filter(c, size=(max(1, per // 3), 3), mode="nearest")
    return c, per, direct_wave_end(d, per)


def surface_polarity(data: np.ndarray, period: int) -> int:
    """Sign (+1/−1) of the direct wave's main lobe, or 0 when there is no direct wave left."""
    d = np.asarray(data, dtype=np.float64)
    end = direct_wave_end(d, period)
    if end <= period:  # nothing strong at the top (already background-removed)
        return 0
    mean = d[:end].mean(axis=1)
    k = int(np.argmax(np.abs(mean)))
    return int(np.sign(mean[k]))


def running_median_excluding(values: np.ndarray, half: int) -> tuple[np.ndarray, np.ndarray]:
    """Median and MAD of each column's neighbours (±half, centre excluded) along the last axis."""
    n = values.shape[-1]
    med = np.empty_like(values)
    mad = np.empty_like(values)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        nb = np.concatenate([values[..., lo:i], values[..., i + 1:hi]], axis=-1)
        m = np.median(nb, axis=-1)
        med[..., i] = m
        mad[..., i] = 1.4826 * np.median(np.abs(nb - m[..., None]), axis=-1)
    return med, mad


def runs(mask: np.ndarray, max_gap: int = 0) -> list[tuple[int, int]]:
    """Inclusive (start, end) index runs of True, bridging gaps of up to max_gap."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    out = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > max_gap + 1:
            out.append((start, prev))
            start = i
        prev = i
    out.append((start, prev))
    return out
