"""Small synthetic B-scan builder for the detect/ tests.

Kept inside tests/ on purpose: gpr_analyzer.synthetic belongs to the processing work and
may model things differently. Everything here is in two-way time (ns) and trace index.
"""

from __future__ import annotations

import numpy as np

from gpr_analyzer import Radargram


def ricker(t_ns: np.ndarray, f_ghz: float) -> np.ndarray:
    a = (np.pi * f_ghz * t_ns) ** 2
    return (1.0 - 2.0 * a) * np.exp(-a)


class Line:
    """Accumulates events into a (samples, traces) array."""

    def __init__(self, n_samples=400, n_traces=300, dt_ns=0.25, dx_m=0.05, v=0.1, f_ghz=0.4, seed=0):
        self.dt_ns, self.dx_m, self.v, self.f = dt_ns, dx_m, v, f_ghz
        self.t = np.arange(n_samples) * dt_ns
        self.data = np.zeros((n_samples, n_traces))
        self.rng = np.random.default_rng(seed)

    @property
    def n_traces(self):
        return self.data.shape[1]

    def _put(self, x: int, tau_ns: float, amp: float):
        self.data[:, x] += amp * ricker(self.t - tau_ns, self.f)

    def direct_wave(self, t_ns=2.0, amp=10.0):
        for x in range(self.n_traces):
            self._put(x, t_ns, amp)
        return self

    def layer(self, t_ns, amp=1.0, x0=0, x1=None, slope_ns_per_trace=0.0):
        x1 = self.n_traces if x1 is None else x1
        for x in range(x0, x1):
            self._put(x, t_ns + slope_ns_per_trace * (x - x0), amp)
        return self

    def point(self, x0, t0_ns, amp=1.0, aperture=40):
        for x in range(max(0, x0 - aperture), min(self.n_traces, x0 + aperture + 1)):
            d = (x - x0) * self.dx_m
            tau = np.sqrt(t0_ns**2 + (2 * d / self.v) ** 2)
            self._put(x, tau, amp * t0_ns / tau)
        return self

    def void(self, x0, x1, t_top, t_bot, amp=1.5, polarity=-1, edges=True, multiples=3, mult_dt=None):
        """Air-filled cavity: roof with reversed polarity, edge diffractions, ringing inside."""
        mult_dt = (t_bot - t_top) / (multiples + 1) if mult_dt is None else mult_dt
        for x in range(x0, x1):
            self._put(x, t_top, polarity * amp)
            for k in range(1, multiples + 1):
                self._put(x, t_top + k * mult_dt, polarity * amp * 0.6**k)
            self._put(x, t_bot, -polarity * amp * 0.5)
        if edges:
            for xe in (x0, x1 - 1):
                for x in range(max(0, xe - 25), min(self.n_traces, xe + 26)):
                    if x0 + 3 <= x <= x1 - 4:
                        continue
                    d = (x - xe) * self.dx_m
                    tau = np.sqrt(t_top**2 + (2 * d / self.v) ** 2)
                    self._put(x, tau, polarity * amp * 0.6 * t_top / tau)
        return self

    def pull_up(self, t_ns, x0, x1, shift_ns, amp=0.8):
        """A deep reflector that arrives earlier beneath (x0, x1) — the fast-cavity effect."""
        for x in range(self.n_traces):
            self._put(x, t_ns - (shift_ns if x0 <= x < x1 else 0.0), amp)
        return self

    def scatter(self, x0, x1, t0, t1, n=14, amp=1.2):
        for _ in range(n):
            xc = int(self.rng.integers(x0, x1))
            tc = float(self.rng.uniform(t0, t1))
            self.point(xc, tc, amp=amp * self.rng.uniform(0.6, 1.0) * self.rng.choice([-1, 1]), aperture=5)
        return self

    def stripe(self, x0, x1, amp=2.0):
        """Full-depth vertical band (antenna lift / surface change)."""
        n = self.data.shape[0]
        ring = amp * np.sin(2 * np.pi * self.f * self.t) * np.exp(-self.t / (self.t[-1] * 3))
        for x in range(x0, x1):
            self.data[:, x] += ring + 0.5 * amp * self.rng.standard_normal(n)
        return self

    def dead(self, x):
        self.data[:, x] = 0.0
        return self

    def stationary(self, x0, x1):
        """Operator stopped: the same trace repeated."""
        self.data[:, x0:x1] = self.data[:, [x0 - 1]]
        return self

    def noise(self, sigma=0.05):
        self.data += sigma * self.rng.standard_normal(self.data.shape)
        return self

    def gained_bottom_noise(self, from_ns, sigma=1.0):
        """Exponentially growing random noise — what a strong gain does past the signal floor."""
        i0 = int(from_ns / self.dt_ns)
        n = self.data.shape[0] - i0
        g = np.exp(np.linspace(0, 2.0, n))[:, None] / np.e
        self.data[i0:] += sigma * g * self.rng.standard_normal((n, self.n_traces))
        return self

    def radargram(self) -> Radargram:
        return Radargram(self.data.copy(), dt_ns=self.dt_ns, dx_m=self.dx_m, velocity_m_per_ns=self.v,
                         metadata={"source": "tests.detect_synth"})
