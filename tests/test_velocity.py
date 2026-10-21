import numpy as np
import pytest

from gpr_analyzer.processing.velocity import estimate_velocity, fit_hyperbola
from gpr_analyzer.synthetic import single_hyperbola


def test_fit_exact_hyperbola():
    v, x0, t0 = 0.12, 3.3, 25.0
    x = np.linspace(1.0, 6.0, 60)
    t = np.sqrt(t0**2 + 4 * (x - x0) ** 2 / v**2)
    f = fit_hyperbola(x, t)
    assert f.velocity_m_per_ns == pytest.approx(v, rel=1e-6)
    assert f.x0_m == pytest.approx(x0, abs=1e-6)
    assert f.t0_ns == pytest.approx(t0, rel=1e-6)
    assert f.depth_m == pytest.approx(v * t0 / 2)
    assert f.r2 > 0.999999


def test_fit_with_pick_noise():
    rng = np.random.default_rng(0)
    v, x0, t0 = 0.1, 5.0, 40.0
    x = np.linspace(3.0, 7.0, 80)
    t = np.sqrt(t0**2 + 4 * (x - x0) ** 2 / v**2) + rng.normal(0, 0.3, x.size)
    f = fit_hyperbola(x, t)
    assert abs(f.velocity_m_per_ns - v) < 0.005


def test_fit_rejects_flat_picks():
    with pytest.raises(ValueError):
        fit_hyperbola(np.linspace(0, 5, 20), np.full(20, 30.0))


@pytest.mark.parametrize("v", [0.06, 0.1, 0.135])
def test_estimate_velocity_single_hyperbola(v):
    rg, truth = single_hyperbola(velocity_m_per_ns=v, depth_m=1.2)
    est, fits = estimate_velocity(rg.data, rg.dt_ns, rg.dx_m, truth.freq_mhz)
    assert est == pytest.approx(v, rel=0.02)
    assert fits[0].x0_m == pytest.approx(5.0, abs=0.05)
    assert fits[0].depth_m == pytest.approx(1.2, rel=0.03)


def test_estimate_velocity_none_on_noise():
    rng = np.random.default_rng(0)
    d = rng.standard_normal((256, 200)).astype(np.float32)
    est, fits = estimate_velocity(d, 0.3, 0.05, 400.0)
    assert est is None and fits == []
