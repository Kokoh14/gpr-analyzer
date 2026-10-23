"""ViewPoint-matched depth, time zero and display chain (synthetic data; real-data checks in test_real_impulseradar.py)."""

import numpy as np
import pytest

from gpr_analyzer.core import Radargram, depth_from_time, time_from_depth
from gpr_analyzer.processing import filters as F
from gpr_analyzer.processing.auto import ProcessingOptions, run_pipeline
from gpr_analyzer.processing.viewpoint import display_gain_curve, viewpoint_display
from gpr_analyzer.synthetic import make_scene


def test_bistatic_depth_matches_viewpoint_marker():
    # 800 MHz CrossOver: S = 0.09 m, v = 0.1 m/ns, marker 66 samples (0.09766 ns) below ZERO LEVEL;
    # ViewPoint's field PDF prints 0.36 m for such a marker, plain v*t/2 would give 0.32 m.
    t = 66 * 0.09765625
    assert depth_from_time(t, 0.1, 0.09) == pytest.approx(0.365, abs=0.002)
    assert depth_from_time(t, 0.1) == pytest.approx(0.322, abs=0.001)


def test_depth_time_round_trip_and_zero():
    d = np.array([-0.1, 0.0, 0.05, 0.5, 2.0])
    np.testing.assert_allclose(depth_from_time(time_from_depth(d, 0.1, 0.28), 0.1, 0.28), d, atol=1e-12)
    assert depth_from_time(0.0, 0.1, 0.28) == 0.0


def test_depth_axis_uses_antenna_separation():
    rg = Radargram(np.zeros((100, 3)), dt_ns=0.2, velocity_m_per_ns=0.1, metadata={"antenna_separation_m": 0.28})
    z = rg.depth_axis()
    assert z[0] == 0.0 and np.all(np.diff(z) > 0)
    assert z[-1] == pytest.approx(depth_from_time(99 * 0.2, 0.1, 0.28))
    assert Radargram(np.zeros((100, 3)), dt_ns=0.2).depth_axis()[-1] == pytest.approx(99 * 0.2 * 0.1 / 2)


def test_gain_grows_and_flattens():
    g, p = display_gain_curve(1100, 0.09765625, 800.0)
    db = 20 * np.log10(g)
    assert db[0] == pytest.approx(0.0) and np.all(np.diff(db) > 0)
    assert db[-1] == pytest.approx(p["max_gain_db"], abs=0.5)
    assert p["fitted_on_mhz"] == 800.0
    assert display_gain_curve(10, 0.2, 450.0)[1]["fitted_on_mhz"] == 400.0


def test_polarity_consistent_picks_do_not_jump_between_lobes():
    # direct wave with a positive and a slightly stronger negative lobe 6 samples later;
    # noise makes |amplitude| prefer either lobe trace by trace
    rng = np.random.default_rng(1)
    n, m = 120, 200
    d = np.zeros((n, m))
    t = np.arange(n)[:, None]
    d += 1.0 * np.exp(-0.5 * ((t - 40) / 1.5) ** 2) - 1.02 * np.exp(-0.5 * ((t - 46) / 1.5) ** 2)
    d *= 1 + 0.05 * rng.standard_normal((1, m))
    d[46] *= 1 + 0.06 * rng.standard_normal(m)
    picks = F.first_break_picks(d, 0.3, 13)
    assert np.ptp(picks) < 1.0


@pytest.fixture(scope="module")
def header_scene():
    rg, truth = make_scene(n_traces=120, n_samples=256)
    rg.metadata["zero_level_sample"] = 20
    rg.metadata["antenna_separation_m"] = 0.28
    return rg


def test_pipeline_uses_header_zero_level(header_scene):
    res = run_pipeline(header_scene)
    tz = next(s for s in res.log if s.step == "time_zero")
    assert tz.params["source"] == "header ZERO LEVEL" and tz.params["time_zero_sample"] == 20
    assert res.analysis.n_samples == header_scene.n_samples - 20
    assert res.viewpoint.data.shape == res.analysis.data.shape
    assert "zero_level_sample" not in res.analysis.metadata  # already applied
    picked = run_pipeline(header_scene, ProcessingOptions(time_zero_source="pick"))
    assert next(s for s in picked.log if s.step == "time_zero").params.get("threshold") == 0.3


def test_header_source_without_zero_level_is_an_error():
    rg, _ = make_scene(n_traces=60, n_samples=128)
    with pytest.raises(ValueError, match="ZERO LEVEL"):
        run_pipeline(rg, ProcessingOptions(time_zero_source="header"))


def test_viewpoint_display_parameters(header_scene):
    out, p = viewpoint_display(header_scene)
    assert out.n_samples == header_scene.n_samples - 20 and out.metadata["track"] == "viewpoint"
    assert p["background_statistic"] == "mean" and p["background_window_traces"] % 2 == 1
    # flat events wider than the window are gone
    assert np.abs(out.data.mean(axis=1)).max() < 0.05 * np.abs(out.data).max()
