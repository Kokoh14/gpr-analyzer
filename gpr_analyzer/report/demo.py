"""Mock survey for report previews and tests.

Modelled on the SubScan sample line (555 traces, ~145 ns window, v ≈ 0.135 m/ns): a weak layer,
a scattered strong-reflection zone at ~5–7.5 m, one point target hyperbola and full-depth
vertical stripes that should be flagged as artifacts. The data is synthetic, not a real survey.
"""

from __future__ import annotations

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.report.schema import (ArtifactRegion, Evidence, LineResult, ProcessingStep, SurveyInfo,
                                        SurveyResult, Zone)


def _ricker(t: np.ndarray, f_ghz: float) -> np.ndarray:
    a = (np.pi * f_ghz * t) ** 2
    return (1 - 2 * a) * np.exp(-a)


def mock_radargram(n_traces: int = 555, n_samples: int = 512, dt_ns: float = 0.2832, v: float = 0.135,
                   dx_m: float = 0.05, seed: int = 0,
                   stripes: tuple[tuple[int, int], ...] = ((199, 202), (239, 241), (365, 385))) -> Radargram:
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) * dt_ns
    x = np.arange(n_traces)
    f = 0.3  # 300 MHz wavelet
    data = np.zeros((n_samples, n_traces))
    # layer at ~35 ns, gently dipping
    for j in x:
        t0 = 35 + 2 * np.sin(j / 90)
        data[:, j] += 0.35 * _ricker(t - t0, f)
    # scattered strong reflections, traces 120–330, 77–111 ns
    for _ in range(140):
        j0 = rng.integers(120, 330)
        t0 = rng.uniform(78, 110)
        amp = rng.uniform(0.3, 0.9) * rng.choice([-1, 1])
        for j in range(max(0, j0 - 25), min(n_traces, j0 + 25)):
            tt = 2 * np.hypot((j - j0) * dx_m, v * t0 / 2) / v
            data[:, j] += amp * np.exp(-((j - j0) / 12) ** 2) * _ricker(t - tt, f)
    # point target hyperbola, apex trace 450 at 42 ns
    for j in x:
        tt = 2 * np.hypot((j - 450) * dx_m, v * 42 / 2) / v
        data[:, j] += 0.8 * np.exp(-abs(j - 450) / 60) * _ricker(t - tt, f)
    # full-depth vertical stripes (antenna lift / surface change)
    for j0, j1 in stripes:
        data[:, j0:j1 + 1] += rng.normal(0, 0.25, (n_samples, j1 - j0 + 1)) * (1 + t[:, None] / 60)
    data += rng.normal(0, 0.04, data.shape) * (1 + t[:, None] / 50)
    return Radargram(data.astype(np.float32), dt_ns=dt_ns, dx_m=dx_m, velocity_m_per_ns=v,
                     metadata={"source": "synthetic mock"})


PROCESSING = [
    ProcessingStep("dewow", {"window_ns": 6.7}),
    ProcessingStep("time_zero", {"method": "first-break", "shift_ns": 1.4}),
    ProcessingStep("bandpass", {"low_mhz": 100, "high_mhz": 600}),
    ProcessingStep("background_removal", {"method": "moving-window mean", "window_traces": 101}),
    ProcessingStep("gain_sec", {"alpha_per_ns": 0.012, "max_gain_db": 40}),
    ProcessingStep("artifact_check", {"stripe_min_depth_fraction": 0.8}),
    ProcessingStep("velocity", {"method": "x²–t² hyperbola fit", "n_hyperbolas": 3}),
    ProcessingStep("envelope", {"method": "Hilbert"}),
    ProcessingStep("agc", {"window_ns": 25}, auto=True),
]


def mock_survey(with_radargram: bool = True, reviewed: bool = False) -> SurveyResult:
    v = 0.135
    dx = 0.05
    r1 = mock_radargram(v=v, dx_m=dx) if with_radargram else None
    r2 = mock_radargram(n_traces=300, v=0.1, dx_m=dx, seed=3, stripes=()) if with_radargram else None
    review = "accepted" if reviewed else "auto"
    z = [
        Zone("A", "L1", 120, 330, time_top_ns=77.0, time_bottom_ns=111.0, velocity_m_per_ns=v,
             velocity_source="hyperbola_fit", velocity_uncertainty_m_per_ns=0.015,
             suspected_type="void_like", confidence="medium",
             distance_start_m=120 * dx, distance_end_m=330 * dx,
             coord_start=(100.523410, 13.736720), coord_end=(100.523530, 13.736805), datum="WGS84",
             evidence=[Evidence("present", "strong_reflection"),
                       Evidence("present", "discontinuous_reflection"),
                       Evidence("absent", "polarity_reversal_top"),
                       Evidence("absent", "pull_up_below"),
                       Evidence("warning", "near_artifact", params={"traces": "365–385"})],
             artifact_flags=["stripe 199–202", "stripe 239–241"], review=review),
        Zone("B", "L1", 430, 470, time_top_ns=40.0, time_bottom_ns=52.0, velocity_m_per_ns=v,
             velocity_source="hyperbola_fit", suspected_type="pipe_point", confidence="high",
             distance_start_m=430 * dx, distance_end_m=470 * dx,
             evidence=[Evidence("present", "single_apex_hyperbola")], review=review),
        Zone("C", "L1", 0, 554, time_top_ns=31.0, time_bottom_ns=39.0, velocity_m_per_ns=v,
             velocity_source="hyperbola_fit", suspected_type="layer", confidence="high",
             distance_start_m=0.0, distance_end_m=554 * dx,
             evidence=[Evidence("present", "continuous_reflector", params={"traces": 555})], review=review),
        Zone("D", "L1", 365, 385, time_top_ns=0.0, time_bottom_ns=145.0, velocity_m_per_ns=v,
             velocity_source="hyperbola_fit", suspected_type="artifact", confidence="high",
             distance_start_m=365 * dx, distance_end_m=385 * dx,
             evidence=[Evidence("present", "vertical_stripe", params={"traces": "365–385"}),
                       Evidence("present", "gain_noise_floor")], review=review),
        Zone("E", "L2", 60, 140, time_top_ns=80.0, time_bottom_ns=100.0, velocity_m_per_ns=0.1,
             velocity_source="default", suspected_type="scattered", confidence="medium",
             distance_start_m=60 * dx, distance_end_m=140 * dx,
             evidence=[Evidence("present", "irregular_scatter"), Evidence("absent", "edge_hyperbola")],
             review=review),
    ]
    l1 = LineResult("L1", "LINE001.iprb", 555, 512, 0.2832, dx, v, "hyperbola_fit", processing=list(PROCESSING),
                    zones=z[:4], radargram=r1,
                    artifact_regions=[ArtifactRegion(199, 202, "vertical_stripe"),
                                      ArtifactRegion(239, 241, "vertical_stripe"),
                                      ArtifactRegion(365, 385, "vertical_stripe")])
    l2 = LineResult("L2", "LINE002.iprb", 300, 512, 0.2832, dx, 0.1, "default",
                    processing=[p for p in PROCESSING if p.name != "velocity"], zones=z[4:], radargram=r2)
    info = SurveyInfo(project="ตัวอย่าง / Demo survey", site="ถนนตัวอย่าง (ข้อมูลจำลอง) / Mock road",
                      client="—", survey_date="2026-10-20", operator="—", instrument="ImpulseRadar (mock)",
                      antenna="300 MHz (mock)", frequency_mhz=300, surface_use="road", datum="WGS84",
                      report_date="2026-10-21", report_id="DEMO-0001")
    return SurveyResult(info=info, lines=[l1, l2])
