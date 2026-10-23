import numpy as np

from gpr_analyzer.synthetic import make_scene, ricker, single_hyperbola


def test_ricker_peak_and_zero_mean():
    tau = np.linspace(-20, 20, 2001)
    w = ricker(tau, 250.0)
    assert np.argmax(w) == 1000 and w[1000] == 1.0
    assert abs(np.trapezoid(w, tau)) < 1e-3


def test_scene_shape_and_truth():
    rg, truth = make_scene()
    assert rg.data.shape == (512, 555)
    assert rg.data.dtype == np.float32
    assert abs(rg.time_axis()[-1] - 145.0) < 1e-9
    assert truth.velocity_m_per_ns == rg.velocity_m_per_ns
    assert truth.artifact_traces == [200, 240, 260, 310, 365, 369, 373, 377, 381, 385]
    assert truth.target_time_ns(truth.targets[0]) == 20.0


def test_scene_is_reproducible():
    a, _ = make_scene(seed=3)
    b, _ = make_scene(seed=3)
    c, _ = make_scene(seed=4)
    assert np.array_equal(a.data, b.data)
    assert not np.array_equal(a.data, c.data)


def test_single_hyperbola_apex_where_expected():
    rg, truth = single_hyperbola(x0_m=5.0, depth_m=1.5, velocity_m_per_ns=0.1)
    i, j = np.unravel_index(np.argmax(np.abs(rg.data)), rg.data.shape)
    assert abs(j - 100) <= 3  # flat apex: neighbours differ only by sampling
    assert abs(np.argmax(rg.data[:, 100]) * rg.dt_ns - 30.0) <= rg.dt_ns
