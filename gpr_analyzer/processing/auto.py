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
from typing import Any, Callable

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.processing import filters as F
from gpr_analyzer.processing.migration import kirchhoff_migration, stolt_migration
from gpr_analyzer.processing.velocity import HyperbolaFit, estimate_velocity
from gpr_analyzer.processing.viewpoint import viewpoint_display

DEFAULT_VELOCITY = 0.1  # m/ns, typical moist soil; used only when no hyperbola fits


@dataclass
class ProcessingOptions:
    """Overrides. None = let the pipeline choose from the data."""

    center_freq_mhz: float | None = None
    dewow_window_ns: float | None = None
    time_zero: bool = True
    time_zero_source: str = "auto"  # "auto" (header ZERO LEVEL if present, else pick) | "header" | "pick"
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
    auto: bool = True  # False when the user set or forced this step

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_report_step(self) -> dict[str, Any]:
        """Dict for report.schema.ProcessingStep(name, params, auto); the reason goes into params."""
        return {"name": self.step, "params": {**self.params, "reason": self.reason}, "auto": self.auto}


@dataclass
class ProcessingResult:
    """Output of run_pipeline.

    analysis / display / envelope / energy share the same grid (time zero at row 0).
    layers: same as analysis but without background removal (keeps flat reflectors).
    When tracks is supplied, unrequested optional outputs are None.
    """

    raw: Radargram
    analysis: Radargram
    display: Radargram | None
    layers: Radargram | None
    envelope: Radargram | None
    energy: Radargram | None
    migrated: Radargram | None
    velocity_m_per_ns: float
    velocity_source: str  # human-readable, e.g. "hyperbola fit (median of 3)"
    velocity_source_code: str  # "hyperbola_fit" | "file_header" | "default" | "user" (report.schema codes)
    hyperbola_fits: list[HyperbolaFit]
    gain_curve: np.ndarray
    noise_floor_sample: int
    log: list[StepLog] = field(default_factory=list)
    viewpoint: Radargram | None = None  # ViewPoint-like screen (processing/viewpoint.py), display only

    def parameter_table(self) -> list[dict[str, Any]]:
        """Rows for the report's processing-parameters section."""
        return [s.to_dict() for s in self.log]

    def report_steps(self) -> list[dict[str, Any]]:
        """Steps as report.schema.ProcessingStep dicts (name, params, auto)."""
        return [s.to_report_step() for s in self.log]


def _odd(n: float) -> int:
    k = max(int(round(n)), 1)
    return k if k % 2 else k + 1


def auto_process(rg: Radargram, options: ProcessingOptions | None = None) -> tuple[Radargram, list[dict[str, Any]]]:
    """CLI / report hook: (analysis-track Radargram, report steps).

    The returned radargram carries metadata["velocity_source"] (report code) and
    metadata["processing"] (same steps). Use run_pipeline for the other tracks.
    A radargram whose metadata["velocity_source"] is "user" keeps its velocity (CLI --velocity).
    """
    if options is None and rg.metadata.get("velocity_source") == "user":
        options = ProcessingOptions(velocity_m_per_ns=rg.velocity_m_per_ns)
    res = run_pipeline(rg, options, tracks={"analysis"})
    out = res.analysis.copy_with()
    steps = res.report_steps()
    out.metadata["velocity_source"] = res.velocity_source_code
    out.metadata["processing"] = steps
    out.metadata["hyperbola_fits"] = [f.to_dict() for f in res.hyperbola_fits]
    return out, steps


class _ReportingLog(list):
    """Step log that also tells a caller which step just finished (for progress displays)."""

    def __init__(self, progress: Callable[[str], None]) -> None:
        super().__init__()
        self._progress = progress

    def append(self, step: StepLog) -> None:  # type: ignore[override]
        super().append(step)
        self._progress(step.step)


def run_pipeline(rg: Radargram, options: ProcessingOptions | None = None,
                 progress: Callable[[str], None] | None = None, *, tracks: set[str] | None = None) -> ProcessingResult:
    """progress reports completed steps. tracks=None keeps all outputs for existing API callers."""
    opt = options or ProcessingOptions()
    tracks = {"analysis", "display", "layers", "envelope", "energy", "viewpoint"} if tracks is None else tracks
    if tracks - {"analysis", "display", "layers", "envelope", "energy", "viewpoint"}:
        raise ValueError("unknown processing track")
    log: list[StepLog] = _ReportingLog(progress) if progress else []
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
    log.append(StepLog("center_frequency", {"center_freq_mhz": round(fc, 1)}, why, opt.center_freq_mhz is None))

    # 2. Dewow
    period = 1e3 / fc
    win = opt.dewow_window_ns or 2.0 * period
    d = F.dewow(d, dt, win)
    log.append(
        StepLog(
            "dewow",
            {"window_ns": round(win, 2), "method": "subtract Gaussian-smoothed trace"},
            "user value" if opt.dewow_window_ns else "2 periods of the centre frequency: removes drift, keeps the antenna band",
            opt.dewow_window_ns is None,
        )
    )

    # 3. Time zero
    zl = rg.metadata.get("zero_level_sample")
    use_header = opt.time_zero and zl and opt.time_zero_source in ("auto", "header")
    if opt.time_zero_source == "header" and not zl:
        raise ValueError("time_zero_source='header' but the file has no ZERO LEVEL")
    if use_header:
        target = int(zl)
        d = F.time_zero_static(d, target)
        log.append(
            StepLog(
                "time_zero",
                {"source": "header ZERO LEVEL", "time_zero_sample": target, "time_zero_ns": round(target * dt, 2)},
                "the acquisition unit's own zero (ViewPoint puts depth 0 there); one shift for all traces, "
                "since the direct wave on real CrossOver lines moves less than 0.2 sample",
                opt.time_zero_source == "auto",
            )
        )
    elif opt.time_zero:
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
        log.append(StepLog("time_zero", {"applied": False}, "disabled by user", False))

    # 4. Bandpass
    if opt.band_mhz:
        lo, hi = opt.band_mhz
        why = "set by user"
    else:
        lo, hi = 0.4 * fc, 2.0 * fc
        why = "0.4x to 2x the centre frequency (Ricker-like antenna band)"
    hi = min(hi, 0.95 * 0.5e3 / dt)
    d = F.bandpass(d, dt, lo, hi)
    log.append(StepLog("bandpass", {"low_mhz": round(lo, 1), "high_mhz": round(hi, 1), "type": "Butterworth order 4, zero-phase"}, why, opt.band_mhz is None))
    no_bgr = d.copy() if "layers" in tracks else None

    # 5. Background removal
    if opt.background == "none":
        log.append(StepLog("background_removal", {"applied": False}, "disabled by user", False))
    else:
        if opt.background == "global" or rg.dx_m is None:
            wtr = None
            why = "global mean trace" + (" (no trace spacing in file)" if rg.dx_m is None and opt.background != "global" else "")
        else:
            # A sliding median removes any flat event wider than half the window, so the window must be
            # at least twice the widest void roof we want to keep (the sample line shows one ~7.5 m wide).
            wm = opt.background_window_m or 30.0
            wtr = _odd(wm / rg.dx_m)
            why = (
                "user window" if opt.background_window_m
                else "30 m sliding window: flat events narrower than 15 m (void roofs) survive; on longer lines it still follows ringing changes"
            )
            if wtr >= d.shape[1]:
                wtr = None
                why += "; line shorter than the window, so the global mean is used"
        d = F.background_removal(d, wtr, opt.background_statistic)
        log.append(StepLog("background_removal", {"window_traces": wtr if wtr else "all", "statistic": opt.background_statistic}, why,
                           opt.background == "sliding" and opt.background_window_m is None and opt.background_statistic == "median"))

    # 6. Amplitude-preserving gain (SEC from the data's own decay)
    decay = F.amplitude_decay(d, dt)
    nf = F.estimate_noise_floor(decay)
    gain, cap, rate_db = F.sec_gain_curve(decay, dt, nf, max_gain_db=opt.gain_max_db)
    analysis = F.apply_gain(d, gain)
    layers = F.apply_gain(no_bgr, gain) if no_bgr is not None else None
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
        v, vsrc, vcode = opt.velocity_m_per_ns, "user", "user"
    else:
        v_est = None
        if opt.estimate_velocity and rg.dx_m is not None:
            v_est, fits = estimate_velocity(analysis, dt, rg.dx_m, fc)
        if v_est is not None:
            v, vsrc, vcode = v_est, f"hyperbola fit (median of {len(fits)})", "hyperbola_fit"
        elif rg.metadata.get("velocity_m_per_ns") or rg.velocity_m_per_ns != DEFAULT_VELOCITY:
            v, vsrc, vcode = rg.velocity_m_per_ns, "file header (no usable hyperbola)", "file_header"
        else:
            v, vsrc, vcode = DEFAULT_VELOCITY, "default 0.1 m/ns (no usable hyperbola) - depths uncertain", "default"
    log.append(
        StepLog(
            "velocity",
            {
                "velocity_m_per_ns": round(v, 4),
                "n_hyperbolas": len(fits),
                "spread_m_per_ns": round(float(np.std([f.velocity_m_per_ns for f in fits])), 4) if fits else None,
            },
            vsrc,
            vcode != "user",
        )
    )

    # 8. Migration (optional)
    migrated = None
    if opt.migration != "none":
        if rg.dx_m is None:
            log.append(StepLog("migration", {"applied": False}, "skipped: file has no trace spacing", False))
        else:
            fn = stolt_migration if opt.migration == "stolt" else kirchhoff_migration
            migrated = fn(analysis, dt, rg.dx_m, v)
            log.append(StepLog("migration", {"method": opt.migration, "velocity_m_per_ns": round(v, 4)}, "requested by user", False))

    # 9. Attributes
    env = F.envelope(analysis) if "envelope" in tracks else None
    ew = opt.energy_window_ns or 2.0 * period
    en = F.energy(analysis, dt, ew) if "energy" in tracks else None
    if env is not None or en is not None:
        log.append(StepLog("envelope", {"envelope": "Hilbert" if env is not None else "not requested",
                                      "energy_window_ns": round(ew, 2) if en is not None else None},
                           "requested attributes computed on the analysis track", opt.energy_window_ns is None))

    # 10. Display track
    aw = opt.agc_window_ns or 10.0 * period
    nrms = 5.0 * F.noise_rms(d, dt, aw) if "display" in tracks else 0.0
    display = F.agc(d, dt, aw, noise_rms=nrms) if "display" in tracks else None
    if display is not None:
        log.append(
            StepLog(
                "agc",
                {"window_ns": round(aw, 1), "noise_stabiliser": float(f"{nrms:.3g}")},
                "display only: AGC equalises amplitude, so it is never used for detection or scoring; stabilised at 5x the noise level so empty areas stay quiet",
                opt.agc_window_ns is None,
            )
        )

    # 11. ViewPoint-like track (same zero as above, its own fitted background window and gain)
    zero_used = int(zl) if use_header else (target if opt.time_zero else 0)
    vp, vp_params = viewpoint_display(rg, zero_sample=zero_used) if "viewpoint" in tracks else (None, {})
    if vp is not None:
        log.append(
            StepLog(
                "viewpoint_display",
                vp_params,
                "display only: reproduces the ImpulseRadar ViewPoint screen (fitted to its screenshots, docs/viewpoint-fit.md)",
            )
        )

    def mk(arr: np.ndarray, kind: str) -> Radargram:
        out = rg.copy_with(data=arr, velocity_m_per_ns=v)
        out.metadata["track"] = kind
        if opt.time_zero:
            out.metadata.pop("zero_level_sample", None)  # row 0 is time zero now
        return out

    return ProcessingResult(
        raw=rg,
        analysis=mk(analysis, "analysis"),
        display=mk(display, "display") if display is not None else None,
        layers=mk(layers, "layers") if layers is not None else None,
        envelope=mk(env, "envelope") if env is not None else None,
        energy=mk(en, "energy") if en is not None else None,
        migrated=mk(migrated, "migrated") if migrated is not None else None,
        velocity_m_per_ns=v,
        velocity_source=vsrc,
        velocity_source_code=vcode,
        hyperbola_fits=fits,
        gain_curve=gain,
        noise_floor_sample=cap,
        log=log,
        viewpoint=mk(vp.data, "viewpoint") if vp is not None else None,
    )
