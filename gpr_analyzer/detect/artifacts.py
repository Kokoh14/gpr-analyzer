"""Artifact flags, kept apart from real targets.

* vertical_stripe: a trace band whose energy differs from its neighbours through most of the
  depth range (antenna lift, surface change, operator stop). A real target is local in depth.
* dead_trace / clipped_trace / stationary_traces: abnormal single traces or runs.
* ringing: a time band where background removal left a speckled residue of the antenna's own
  ringing (or another flat event) on most traces. It looks like a row of small bright spots.
* bottom_noise: the incoherent tail of the record where a gain has amplified noise; red there
  in an energy view is gain, not a target.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d

from gpr_analyzer.core import Radargram
from gpr_analyzer.detect._signal import (
    dominant_period_samples,
    envelope,
    remove_background,
    running_median_excluding,
    runs,
)
from gpr_analyzer.detect.models import ArtifactFlag


@dataclass
class ArtifactParams:
    stripe_windows: int = 8
    stripe_neighbour_half: int = 25
    stripe_guard: int = 12  # neighbours start this many traces away, so wide bands (e.g. 365–385) still stand out
    stripe_z: float = 3.5
    stripe_min_fraction: float = 0.5  # share of depth windows where the band stands out
    dead_rms_ratio: float = 1e-3
    clip_run: int = 3
    stationary_diff_ratio: float = 0.25
    stationary_min_traces: int = 6  # a few near-identical neighbours are common on quiet ground
    noise_coherence: float = 0.4
    noise_rise: float = 1.5  # noise envelope must grow by this factor to count as gained
    noise_min_fraction: float = 0.05
    ringing_min_fraction: float = 0.15  # share of traces bright in short pieces, per time sample
    ringing_segments: int = 5  # specks must be in all but one of this many equal parts of the line
    ringing_short_traces: int = 15  # pieces shorter than this count as speckle; longer ones are reflectors
    ringing_contrast: float = 4.0
    ringing_max_periods: float = 3.0  # thicker bands of specks are crossing events, not ringing
    ringing_factor: float = 2.0  # inside a band, contrast counts from this × the row's 90th percentile up


def _flag(kind, n, a, b, t0, t1, th, en, strength=0.0) -> ArtifactFlag:
    prefix = {"vertical_stripe": "VS", "dead_trace": "DT", "clipped_trace": "CT",
              "stationary_traces": "ST", "bottom_noise": "BN", "ringing": "RB"}[kind]
    return ArtifactFlag(f"{prefix}{n}", kind, int(a), int(b), float(t0), float(t1), th, en, float(strength))


def _dead_mask(data: np.ndarray, p: ArtifactParams) -> np.ndarray:
    rms = np.sqrt(np.mean(data.astype(np.float64) ** 2, axis=0))
    ref = np.median(rms[rms > 0]) if np.any(rms > 0) else 0.0
    return rms <= p.dead_rms_ratio * ref


def detect_vertical_stripes(r: Radargram, params: ArtifactParams | None = None) -> list[ArtifactFlag]:
    p = params or ArtifactParams()
    n_s, n_t = r.data.shape
    if n_t < 8:
        return []
    env = envelope(remove_background(r.data.astype(np.float64)))
    top = max(1, n_s // 20)
    edges = np.linspace(top, n_s, p.stripe_windows + 1).astype(int)
    win = np.stack([env[a:b].mean(axis=0) for a, b in zip(edges[:-1], edges[1:])])
    logw = np.log10(np.maximum(win, 1e-12))
    dead = _dead_mask(r.data, p)
    # neighbour statistics ignore dead traces (they are flagged on their own)
    filled = logw.copy()
    if dead.any() and (~dead).any():
        good = np.flatnonzero(~dead)
        for w in range(filled.shape[0]):
            filled[w, dead] = np.interp(np.flatnonzero(dead), good, logw[w, good])
    half = min(p.stripe_neighbour_half, max(3, n_t // 4))
    guard = min(p.stripe_guard, max(0, n_t // 8))
    med, mad = running_median_excluding(filled, half, guard)
    z = (filled - med) / np.maximum(mad, 0.05)
    frac = (np.abs(z) > p.stripe_z).mean(axis=0)
    mask = (frac >= p.stripe_min_fraction) & ~dead
    out = []
    t_end = (n_s - 1) * r.dt_ns
    for i, (a, b) in enumerate(runs(mask, max_gap=1), 1):
        zz = float(np.median(z[:, a:b + 1]))
        sign = "สว่าง" if zz > 0 else "มืด"
        out.append(_flag(
            "vertical_stripe", i, a, b, 0.0, t_end,
            f"แถบแนวตั้ง{sign} trace {a}–{b} ยาวตลอดความลึก น่าจะเป็นเสาอากาศยก ผิวเปลี่ยน หรือหยุดเดิน",
            f"{'Bright' if zz > 0 else 'Dark'} vertical band, traces {a}–{b}, through the full depth: "
            "likely antenna lift-off, surface change or a stop",
            abs(zz)))
    return out


def detect_abnormal_traces(r: Radargram, params: ArtifactParams | None = None) -> list[ArtifactFlag]:
    p = params or ArtifactParams()
    d = r.data.astype(np.float64)
    n_s, n_t = d.shape
    t_end = (n_s - 1) * r.dt_ns
    out: list[ArtifactFlag] = []
    dead = _dead_mask(d, p)
    for i, (a, b) in enumerate(runs(dead), 1):
        out.append(_flag("dead_trace", i, a, b, 0.0, t_end,
                         f"trace {a}–{b} ไม่มีสัญญาณ (ค่าเป็นศูนย์)" if a != b else f"trace {a} ไม่มีสัญญาณ",
                         f"Trace(s) {a}–{b} carry no signal" if a != b else f"Trace {a} carries no signal"))

    peak = np.max(np.abs(d)) if d.size else 0.0
    if peak > 0:
        at_max = np.abs(d) >= 0.999 * peak
        k = np.ones(p.clip_run)
        clipped = np.array([np.any(np.convolve(at_max[:, x], k, "valid") >= p.clip_run) for x in range(n_t)])
        for i, (a, b) in enumerate(runs(clipped), 1):
            out.append(_flag("clipped_trace", i, a, b, 0.0, t_end,
                             f"trace {a}–{b} สัญญาณอิ่มตัว (clipping) แอมพลิจูดเชื่อถือไม่ได้",
                             f"Trace(s) {a}–{b} are clipped; amplitudes are unreliable"))

    if n_t >= 4:
        diff = np.sqrt(np.mean(np.diff(d, axis=1) ** 2, axis=0))  # between trace i and i+1
        ref = np.median(diff[diff > 0]) if np.any(diff > 0) else 0.0
        same = (diff <= p.stationary_diff_ratio * ref) & ~dead[1:] & ~dead[:-1]
        n = 0
        for a, b in runs(same):
            ta, tb = a, b + 1  # pair index → trace range
            if tb - ta + 1 >= p.stationary_min_traces:
                n += 1
                out.append(_flag("stationary_traces", n, ta, tb, 0.0, t_end,
                                 f"trace {ta}–{tb} แทบเหมือนกัน น่าจะหยุดเดินหรือล้อวัดระยะไม่หมุน",
                                 f"Traces {ta}–{tb} are nearly identical: likely stopped or odometer slip"))
    return out


def _lateral_coherence(d: np.ndarray, w: int, max_lag: int) -> np.ndarray:
    """Per-sample median over trace pairs of the best normalised correlation within ±max_lag
    samples: dipping events (hyperbola limbs) stay coherent, random noise does not."""
    a = d[:, :-1]
    best = np.full(a.shape, -np.inf)
    ea = uniform_filter1d(a * a, w, axis=0)
    for lag in range(-max_lag, max_lag + 1):
        b = np.roll(d[:, 1:], lag, axis=0)
        num = uniform_filter1d(a * b, w, axis=0)
        den = np.sqrt(ea * uniform_filter1d(b * b, w, axis=0))
        best = np.maximum(best, num / np.maximum(den, 1e-12))
    return uniform_filter1d(np.median(best, axis=1), w, mode="nearest")


def detect_bottom_noise(r: Radargram, params: ArtifactParams | None = None, *,
                        env: np.ndarray | None = None, period: int | None = None) -> list[ArtifactFlag]:
    p = params or ArtifactParams()
    d = r.data.astype(np.float64)
    n_s, n_t = d.shape
    if n_t < 4 or n_s < 20:
        return []
    per = dominant_period_samples(d) if period is None else period
    w = max(3, 4 * per)
    coh = _lateral_coherence(d, w, max_lag=max(1, per // 3))
    coherent = np.flatnonzero(coh >= p.noise_coherence)
    s0 = int(coherent[-1]) + 1 if coherent.size else 0
    if n_s - s0 < p.noise_min_fraction * n_s:
        return []
    m = uniform_filter1d(np.median(envelope(d) if env is None else env, axis=1), max(3, per), mode="nearest")
    region = m[s0:n_s - w]  # Hilbert edge effects inflate the last few samples
    if region.size < p.noise_min_fraction * n_s:
        return []
    i_min = int(np.argmin(region))
    base = region[i_min]
    if base <= 0 or region[-max(1, len(region) // 8):].mean() < p.noise_rise * base:
        return []
    # onset = where the noise rises above rise × base for good (a bright target is a bump that falls back)
    below = np.flatnonzero(region[i_min:] <= p.noise_rise * base)
    onset = s0 + i_min + (int(below[-1]) + 1 if below.size else 0)
    if n_s - onset < p.noise_min_fraction * n_s:
        return []
    t0 = onset * r.dt_ns
    t1 = (n_s - 1) * r.dt_ns
    return [_flag("bottom_noise", 1, 0, n_t - 1, t0, t1,
                  f"ตั้งแต่ {t0:.0f} ns ลงไปเป็น noise ที่ถูก gain ขยาย ไม่มีความต่อเนื่องระหว่าง trace "
                  "สีแดงในช่วงนี้ไม่ใช่เป้าหมาย",
                  f"From {t0:.0f} ns down the record is gain-amplified incoherent noise; "
                  "high amplitude here is not a target",
                  float(region[-1] / base))]


def ringing_rows(c: np.ndarray, top: int, params: ArtifactParams | None = None,
                 skip: np.ndarray | None = None) -> np.ndarray:
    """Boolean per time sample: rows where many traces are bright only in short, separate pieces.

    A real reflector is bright over long runs of traces (or is local); a residue of flat ringing
    after background removal is bright on a large share of traces but broken into specks, because
    background removal leaves only the trace-to-trace jitter of it. skip: trace columns left out
    (already flagged as stripes or dead traces).
    """
    p = params or ArtifactParams()
    n_s, n_t = c.shape
    keep = np.ones(n_t, dtype=bool) if skip is None else ~skip
    bright = (c >= p.ringing_contrast) & keep[None, :]
    out = np.zeros(n_s, dtype=bool)
    if keep.sum() < p.ringing_segments * p.ringing_short_traces:
        return out
    seg = np.array_split(np.flatnonzero(keep), p.ringing_segments)
    for i in np.flatnonzero(bright[:, keep].mean(axis=1) >= p.ringing_min_fraction):
        if i < top:
            continue
        d = np.diff(np.concatenate(([0], bright[i].astype(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        short = np.zeros(n_t, dtype=bool)
        for a, b in zip(starts, ends):
            if b - a < p.ringing_short_traces:
                short[a:b] = True
        # ringing is along the whole line; a field of rubble or crossing limbs is local
        spread = sum(short[g].mean() >= p.ringing_min_fraction / 2 for g in seg) >= p.ringing_segments - 1
        out[i] = short[keep].mean() >= p.ringing_min_fraction and spread
    return out


def detect_ringing(r: Radargram, c: np.ndarray, period: int, top: int,
                   params: ArtifactParams | None = None, skip: np.ndarray | None = None) -> list[ArtifactFlag]:
    """Time bands of ringing residue (see ringing_rows), widened by half a period each side. A band
    thicker than ringing_max_periods is not ringing (that many rows of specks are crossing events)."""
    p = params or ArtifactParams()
    rows = ringing_rows(c, top, p, skip)
    out = []
    n_s, n_t = c.shape
    for a, b in runs(rows, max_gap=max(1, period // 2)):
        if b - a + 1 > p.ringing_max_periods * period:
            continue
        a, b = max(0, a - period // 2), min(n_s - 1, b + period // 2)
        t0, t1 = a * r.dt_ns, b * r.dt_ns
        out.append(_flag("ringing", len(out) + 1, 0, n_t - 1, t0, t1,
                         f"แถบแนวนอน {t0:.1f}–{t1:.1f} ns เป็นจุดสว่างเล็ก ๆ เรียงตลอดเส้น น่าจะเป็น ringing ของเสาอากาศ"
                         "ที่เหลือจากการลบพื้นหลัง ไม่ใช่เป้าหมาย",
                         f"Horizontal band {t0:.1f}–{t1:.1f} ns of small bright specks along the line: likely antenna "
                         "ringing left over by background removal, not a target"))
    return out


def suppress_ringing(c: np.ndarray, flags: list[ArtifactFlag], dt_ns: float,
                     params: ArtifactParams | None = None) -> np.ndarray:
    """Contrast with ringing bands scaled down, so only what stands well above the specks is bright
    there: a pipe apex crossing the band stays, the specks drop below the threshold. Hyperbola apexes
    and zones are then not pulled into the band."""
    p = params or ArtifactParams()
    out = c
    for a in flags:
        if a.kind != "ringing":
            continue
        if out is c:
            out = c.copy()
        s0, s1 = int(a.time_start_ns / dt_ns), int(a.time_end_ns / dt_ns) + 1
        ref = p.ringing_factor * np.percentile(c[s0:s1], 90, axis=1, keepdims=True) / p.ringing_contrast
        out[s0:s1] = c[s0:s1] / np.maximum(ref, 1.0)
    return out


def detect_artifacts(r: Radargram, params: ArtifactParams | None = None, *,
                     env: np.ndarray | None = None, period: int | None = None) -> list[ArtifactFlag]:
    p = params or ArtifactParams()
    return detect_vertical_stripes(r, p) + detect_abnormal_traces(r, p) + detect_bottom_noise(r, p, env=env, period=period)
