"""Pure-numpy synthetic radargrams with known ground truth.

The scene is built in two-way time, after the direct wave, so every event has an
exact answer that tests can check against: hyperbola apex position and velocity,
layer times, the ringing band, artifact traces and the noise floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from gpr_analyzer.core import Radargram


def ricker(tau_ns: np.ndarray, freq_mhz: float) -> np.ndarray:
    """Ricker wavelet, peak 1 at tau = 0."""
    a = (np.pi * freq_mhz * 1e-3 * tau_ns) ** 2
    return (1.0 - 2.0 * a) * np.exp(-a)


@dataclass
class PointTarget:
    """Point diffractor (pipe, boulder) at x_m along the line and depth_m below the surface."""

    x_m: float
    depth_m: float
    amplitude: float = 1.0


@dataclass
class Layer:
    """Reflector at depth_m (+ optional dip in m per m) with given reflection amplitude."""

    depth_m: float
    amplitude: float = 0.5
    dip: float = 0.0


@dataclass
class Cavity:
    """Air-filled void between x_start_m and x_end_m, roof at depth_top_m, floor at depth_bottom_m.

    Air is faster and has lower permittivity than soil, so the roof reflection has reversed polarity,
    the floor has normal polarity, and energy rings inside the void (multiples). The roof corners
    diffract, giving a hyperbola tail at each edge. Ringing multiples are spaced by the air two-way
    time, which is far shorter than in soil, so `multiples` of them fit between roof and floor.
    """

    x_start_m: float
    x_end_m: float
    depth_top_m: float
    depth_bottom_m: float
    amplitude: float = 1.0
    multiples: int = 3


@dataclass
class SyntheticTruth:
    """Ground truth of a synthetic scene, in the same units the pipeline reports."""

    velocity_m_per_ns: float
    freq_mhz: float
    time_zero_ns: float
    targets: list[PointTarget] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    cavities: list[Cavity] = field(default_factory=list)
    ringing_ns: float | None = None
    artifact_traces: list[int] = field(default_factory=list)
    noise_std: float = 0.0

    def target_time_ns(self, t: PointTarget) -> float:
        """Apex two-way time of a target, measured from the direct wave."""
        return 2.0 * t.depth_m / self.velocity_m_per_ns

    def target_trace(self, t: PointTarget, dx_m: float) -> int:
        return int(round(t.x_m / dx_m))


def make_scene(
    n_traces: int = 555,
    n_samples: int = 512,
    time_window_ns: float = 145.0,
    dx_m: float = 0.05,
    velocity_m_per_ns: float = 0.1,
    freq_mhz: float = 250.0,
    targets: list[PointTarget] | None = None,
    layers: list[Layer] | None = None,
    time_zero_ns: float = 6.0,
    time_zero_jitter_ns: float = 0.6,
    direct_wave_amplitude: float = 3.0,
    ringing_ns: float | None = 35.0,
    ringing_amplitude: float = 0.6,
    artifact_traces: list[int] | None = None,
    artifact_amplitude: float = 0.8,
    wow_amplitude: float = 0.5,
    attenuation_db_per_ns: float = 0.25,
    noise_std: float = 0.002,
    seed: int = 0,
    cavities: list[Cavity] | None = None,
) -> tuple[Radargram, SyntheticTruth]:
    """Build a radargram resembling the SubScan sample line (555 traces, ~145 ns).

    Events, all with known parameters:
    - direct wave at time_zero_ns with per-trace jitter (time-zero correction test)
    - point targets -> diffraction hyperbolas t(x) = 2 sqrt(d^2 + (x-x0)^2) / v
    - layers -> continuous horizontal or dipping reflectors
    - cavities -> reversed-polarity roof, ringing multiples, floor and edge diffractions
    - ringing band: identical horizontal event on every trace (background removal test)
    - artifact traces: full-depth vertical stripes (antenna lift / stop)
    - wow: slow DC drift per trace (dewow test)
    - attenuation + random noise: deep part is noise once gain is applied
    """
    rng = np.random.default_rng(seed)
    if targets is None:
        targets = [
            PointTarget(x_m=5.0, depth_m=1.0, amplitude=1.0),
            PointTarget(x_m=12.0, depth_m=2.5, amplitude=1.0),
            PointTarget(x_m=20.0, depth_m=4.0, amplitude=1.2),
        ]
    if layers is None:
        layers = [Layer(depth_m=3.2, amplitude=0.4, dip=0.02)]
    if artifact_traces is None:
        artifact_traces = [200, 240, 260, 310] + list(range(365, 386, 4))

    dt = time_window_ns / (n_samples - 1)
    t = np.arange(n_samples)[:, None] * dt  # (samples, 1)
    x = np.arange(n_traces)[None, :] * dx_m  # (1, traces)
    v = velocity_m_per_ns

    tz = time_zero_ns + rng.normal(0.0, time_zero_jitter_ns, size=(1, n_traces))
    tz = np.clip(tz, 0.0, None)

    # Subsurface events (relative to time zero), then attenuated with time.
    sub = np.zeros((n_samples, n_traces))
    for tg in targets:
        tt = 2.0 * np.sqrt(tg.depth_m**2 + (x - tg.x_m) ** 2) / v
        # 1/r spreading from the diffractor, with a mild directivity taper on the flanks
        cos_theta = tg.depth_m / np.sqrt(tg.depth_m**2 + (x - tg.x_m) ** 2)
        sub += tg.amplitude * cos_theta * ricker(t - tz - tt, freq_mhz)
    for ly in layers:
        tl = 2.0 * (ly.depth_m + ly.dip * (x - x.mean())) / v
        sub += ly.amplitude * ricker(t - tz - tl, freq_mhz)
    cavities = list(cavities or [])
    for cv in cavities:
        inside = (x >= cv.x_start_m) & (x <= cv.x_end_m)  # (1, traces)
        t_top = 2.0 * cv.depth_top_m / v
        t_bot = 2.0 * cv.depth_bottom_m / v
        step = (t_bot - t_top) / (cv.multiples + 1)
        ev = -cv.amplitude * ricker(t - tz - t_top, freq_mhz)
        for k in range(1, cv.multiples + 1):
            ev += -cv.amplitude * 0.6**k * ricker(t - tz - t_top - k * step, freq_mhz)
        ev += 0.5 * cv.amplitude * ricker(t - tz - t_bot, freq_mhz)
        sub += ev * inside
        for xe in (cv.x_start_m, cv.x_end_m):
            outside = (x < cv.x_start_m) if xe == cv.x_start_m else (x > cv.x_end_m)
            outside = outside & (np.abs(x - xe) <= cv.depth_top_m)  # corner diffractions fade within ~45 degrees
            r = np.sqrt(cv.depth_top_m**2 + (x - xe) ** 2)
            sub += -0.6 * cv.amplitude * (cv.depth_top_m / r) * ricker(t - tz - 2.0 * r / v, freq_mhz) * outside
    rel_t = np.clip(t - tz, 0.0, None)
    sub *= 10.0 ** (-attenuation_db_per_ns * rel_t / 20.0)

    data = sub
    # Direct (air/ground) wave: strong, identical shape on every trace.
    data += direct_wave_amplitude * ricker(t - tz, freq_mhz)
    if ringing_ns is not None:
        data += ringing_amplitude * ricker(t - tz - ringing_ns, freq_mhz * 0.9) * np.ones_like(x)
    # Wow: slow per-trace drift that decays with time.
    wow_level = wow_amplitude * (1.0 + 0.3 * rng.standard_normal((1, n_traces)))
    data += wow_level * np.exp(-t / 60.0)
    for i in artifact_traces:
        if 0 <= i < n_traces:
            # Broadband stripe through the whole time window (system noise burst).
            data[:, i] += artifact_amplitude * 0.05 * rng.standard_normal(n_samples)
            data[:, i] += artifact_amplitude * 0.05 * np.sin(2 * np.pi * freq_mhz * 1e-3 * t[:, 0] * 0.8)
    data += noise_std * rng.standard_normal(data.shape)

    rg = Radargram(
        data=data.astype(np.float32),
        dt_ns=dt,
        dx_m=dx_m,
        velocity_m_per_ns=v,
        metadata={
            "source": "synthetic",
            "antenna_frequency_mhz": freq_mhz,
            "time_window_ns": time_window_ns,
        },
    )
    truth = SyntheticTruth(
        velocity_m_per_ns=v,
        freq_mhz=freq_mhz,
        time_zero_ns=time_zero_ns,
        targets=list(targets),
        layers=list(layers),
        cavities=cavities,
        ringing_ns=ringing_ns,
        artifact_traces=[i for i in artifact_traces if 0 <= i < n_traces],
        noise_std=noise_std,
    )
    return rg, truth


def single_hyperbola(
    x0_m: float = 5.0,
    depth_m: float = 1.5,
    velocity_m_per_ns: float = 0.1,
    n_traces: int = 201,
    n_samples: int = 256,
    time_window_ns: float = 80.0,
    dx_m: float = 0.05,
    freq_mhz: float = 400.0,
) -> tuple[Radargram, SyntheticTruth]:
    """A clean single diffraction (time zero already at sample 0), for velocity / migration tests."""
    return make_scene(
        n_traces=n_traces,
        n_samples=n_samples,
        time_window_ns=time_window_ns,
        dx_m=dx_m,
        velocity_m_per_ns=velocity_m_per_ns,
        freq_mhz=freq_mhz,
        targets=[PointTarget(x_m=x0_m, depth_m=depth_m)],
        layers=[],
        time_zero_ns=0.0,
        time_zero_jitter_ns=0.0,
        direct_wave_amplitude=0.0,
        ringing_ns=None,
        artifact_traces=[],
        wow_amplitude=0.0,
        attenuation_db_per_ns=0.0,
        noise_std=0.0,
    )


def subscan_like_line(seed: int = 7) -> tuple[Radargram, SyntheticTruth]:
    """The end-to-end demo line: shaped like the SubScan sample (555 traces at 5 cm, ~145 ns, 250 MHz,
    v = 0.135 m/ns) with a pipe, a layer, a void-like cavity near traces 150-300 at about 5 m and the
    stripe band around traces 365-385 seen on the sample screen."""
    return make_scene(
        velocity_m_per_ns=0.135,
        targets=[PointTarget(x_m=3.0, depth_m=1.2, amplitude=1.0), PointTarget(x_m=22.0, depth_m=2.4, amplitude=1.0)],
        layers=[Layer(depth_m=2.0, amplitude=0.35, dip=0.01)],
        cavities=[Cavity(x_start_m=7.5, x_end_m=15.0, depth_top_m=5.25, depth_bottom_m=7.3, amplitude=2.5)],
        artifact_traces=[200, 240, 260, 310] + list(range(365, 386)),
        attenuation_db_per_ns=0.12,
        noise_std=0.003,
        seed=seed,
    )
