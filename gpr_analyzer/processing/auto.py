"""Automatic processing pipeline with data-driven defaults and a parameter log.

Every step records what it did, with which values and why, so the report can
list them and a user can override any of them (ProcessingOptions).

Two output tracks:
- analysis: amplitude-preserving (SEC gain derived from the data's own decay,
  capped at the noise floor). Detection and scoring read this one.
- display: AGC on top, for the eye only. Never score on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.processing import filters as F
from gpr_analyzer.processing.migration import kirchhoff_migration, stolt_migration
from gpr_analyzer.processing.velocity import HyperbolaFit, estimate_velocity

DEFAULT_VELOCITY = 0.1  # m/ns, typical moist soil; used only when no hyperbola fits


@dataclass
class ProcessingOptions:
    """Overrides. None = let the pipeline choose from the data."""

    center_freq_mhz: float | None = None
    dewow_window_ns: float | None = None
    time_zero: bool = True
    time_zero_threshold: float = 0.3
    band_mhz: tuple[float, float] | None = None
    background: str = "sliding"  # "sliding" | "global" | "none"
    background_statistic: str = "median"  # "median" | "mean"
    background_window_m: float | None = None
    gain_max_db: float = 60.0
    agc_window_ns: float | None = None
    velocity_m_per_ns: float | None = None
    estimate_velocity: bool = True
    migration: str = "none"  # "none" | "stolt" | "kirchhoff"
    energy_window_ns: float | None = None


@dataclass
class StepLog:
    step: str
    params: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessingResult:
    """Output of run_pipeline.

    analysis / display / envelope / energy share the same grid (time zero at row 0).
    layers: same as analysis but without background removal (keeps flat reflectors).
    """

    raw: Radargram
    analysis: Radargram
    display: Radargram
    layers: Radargram
    envelope: Radargram
    energy: Radargram
    migrated: Radargram | None
    velocity_m_per_ns: float
    velocity_source: str
    hyperbola_fits: list[HyperbolaFit]
    gain_curve: np.ndarray
    noise_floor_sample: int
    log: list[StepLog] = field(default_factory=list)

    def parameter_table(self) -> list[dict[str, Any]]:
        """Rows for the report's processing-parameters section."""
        return [s.to_dict() for s in self.log]


def _odd(n: float) -> int:
    k = max(int(round(n)), 1)
    return k if k % 2 else k + 1


def run_pipeline(rg: Radargram, options: ProcessingOptions | None = None) -> ProcessingResult:
    opt = options or ProcessingOptions()
    log: list[StepLog] = []
    dt = rg.dt_ns
    d = rg.data.astype(np.float32)

    # 1. Antenna frequency: header if present, else measured.
    if opt.center_freq_mhz is not None:
        fc, why = opt.center_freq_mhz, "set by user"
    else:
        fc = F.estimate_center_frequency(d, dt)
        nominal = rg.metadata.get("antenna_frequency_mhz")
        why = "peak of the mean amplitude spectrum"
        if nominal:
            why += f" (header says {nominal} MHz nominal)"
    log.append(StepLog("center_frequency", {"center_freq_mhz": round(fc, 1)}, why))

    # 2. Dewow
    period = 1e3 / fc
    win = opt.dewow_window_ns or 2.0 * period
    d = F.dewow(d, dt, win)
    log.append(
        StepLog(
            "dewow",
            {"window_ns": round(win, 2), "method": "subtract Gaussian-smoothed trace"},
            "user value" if opt.dewow_window_ns else "2 periods of the centre frequency: removes drift, keeps the antenna band",
        )
    )

    # 3. Time zero
    if opt.time_zero:
        picks = F.first_break_picks(d, opt.time_zero_threshold, search_samples=max(int(round(period / dt)), 2))
        target = int(round(float(np.median(picks))))
        d = F.time_zero_correct(d, picks, target)
        log.append(
            StepLog(
                "time_zero",
                {
                    "threshold": opt.time_zero_threshold,
                    "time_zero_ns": round(target * dt, 2),
                    "max_shift_ns": round(float(np.max(np.abs(picks - target))) * dt, 2),
                },
                "aligned the direct-wave peak of every trace and cut the samples above it",
            )
        )
    else:
        log.append(StepLog("time_zero", {"applied": False}, "disabled by user"))

    # 4. Bandpass
    if opt.band_mhz:
        lo, hi = opt.band_mhz
        why = "set by user"
    else:
        lo, hi = 0.4 * fc, 2.0 * fc
        why = "0.4x to 2x the centre frequency (Ricker-like antenna band)"
    hi = min(hi, 0.95 * 0.5e3 / dt)
    d = F.bandpass(d, dt, lo, hi)
    log.append(StepLog("bandpass", {"low_mhz": round(lo, 1), "high_mhz": round(hi, 1), "type": "Butterworth order 4, zero-phase"}, why))
    no_bgr = d.copy()

    # 5. Background removal
    if opt.background == "none":
        log.append(StepLog("background_removal", {"applied": False}, "disabled by user"))
    else:
        if opt.background == "global" or rg.dx_m is None:
            wtr = None
            why = "global mean trace" + (" (no trace spacing in file)" if rg.dx_m is None and opt.background != "global" else "")
        else:
            wm = opt.background_window_m or 10.0
            wtr = _odd(wm / rg.dx_m)
            why = (
                "user window" if opt.background_window_m
                else "10 m sliding window: wider than the hyperbolas expected in the time window, narrow enough to follow ringing changes"
            )
            if wtr >= d.shape[1]:
                wtr = None
                why += "; line shorter than the window, so the global mean is used"
        d = F.background_removal(d, wtr, opt.background_statistic)
        log.append(StepLog("background_removal", {"window_traces": wtr if wtr else "all", "statistic": opt.background_statistic}, why))

    # 6. Amplitude-preserving gain (SEC from the data's own decay)
    decay = F.amplitude_decay(d, dt)
    nf = F.estimate_noise_floor(decay)
    gain, cap, rate_db = F.sec_gain_curve(decay, dt, nf, max_gain_db=opt.gain_max_db)
    analysis = F.apply_gain(d, gain)
    layers = F.apply_gain(no_bgr, gain)
    log.append(
        StepLog(
            "gain_sec",
            {
                "decay_rate_db_per_ns": round(rate_db, 3),
                "noise_floor_ns": round(cap * dt, 1),
                "max_gain_db": round(float(20 * np.log10(gain.max())), 1),
                "gain_cap_db": opt.gain_max_db,
            },
            "exponential gain matching the measured envelope decay, held constant below the time where the signal reaches 2x the noise floor so deep noise is not amplified",
        )
    )

    # 7. Velocity
    fits: list[HyperbolaFit] = []
    if opt.velocity_m_per_ns is not None:
        v, vsrc = opt.velocity_m_per_ns, "user"
    else:
        v_est = None
        if opt.estimate_velocity and rg.dx_m is not None:
            v_est, fits = estimate_velocity(analysis, dt, rg.dx_m, fc)
        if v_est is not None:
            v, vsrc = v_est, f"hyperbola fit (median of {len(fits)})"
        elif rg.metadata.get("velocity_m_per_ns") or rg.velocity_m_per_ns != DEFAULT_VELOCITY:
            v, vsrc = rg.velocity_m_per_ns, "file header (no usable hyperbola)"
        else:
            v, vsrc = DEFAULT_VELOCITY, "default 0.1 m/ns (no usable hyperbola) - depths uncertain"
    log.append(
        StepLog(
            "velocity",
            {
                "velocity_m_per_ns": round(v, 4),
                "n_hyperbolas": len(fits),
                "spread_m_per_ns": round(float(np.std([f.velocity_m_per_ns for f in fits])), 4) if fits else None,
            },
            vsrc,
        )
    )

    # 8. Migration (optional)
    migrated = None
    if opt.migration != "none":
        if rg.dx_m is None:
            log.append(StepLog("migration", {"applied": False}, "skipped: file has no trace spacing"))
        else:
            fn = stolt_migration if opt.migration == "stolt" else kirchhoff_migration
            migrated = fn(analysis, dt, rg.dx_m, v)
            log.append(StepLog("migration", {"method": opt.migration, "velocity_m_per_ns": round(v, 4)}, "requested by user"))

    # 9. Attributes
    env = F.envelope(analysis)
    ew = opt.energy_window_ns or 2.0 * period
    en = F.energy(analysis, dt, ew)
    log.append(StepLog("attributes", {"envelope": "Hilbert", "energy_window_ns": round(ew, 2)}, "computed on the analysis track"))

    # 10. Display track
    aw = opt.agc_window_ns or 10.0 * period
    nrms = 5.0 * F.noise_rms(d, dt, aw)
    display = F.agc(d, dt, aw, noise_rms=nrms)
    log.append(
        StepLog(
            "agc_display",
            {"window_ns": round(aw, 1), "noise_stabiliser": float(f"{nrms:.3g}")},
            "display only: AGC equalises amplitude, so it is never used for detection or scoring; stabilised at 5x the noise level so empty areas stay quiet",
        )
    )

    def mk(arr: np.ndarray, kind: str) -> Radargram:
        out = rg.copy_with(data=arr, velocity_m_per_ns=v)
        out.metadata["track"] = kind
        return out

    return ProcessingResult(
        raw=rg,
        analysis=mk(analysis, "analysis"),
        display=mk(display, "display"),
        layers=mk(layers, "layers"),
        envelope=mk(env, "envelope"),
        energy=mk(en, "energy"),
        migrated=mk(migrated, "migrated") if migrated is not None else None,
        velocity_m_per_ns=v,
        velocity_source=vsrc,
        hyperbola_fits=fits,
        gain_curve=gain,
        noise_floor_sample=cap,
        log=log,
    )
