"""Automatic display filters (processing/autodisplay.py) on synthetic lines with known content."""

import numpy as np
import pytest

from gpr_analyzer.processing import filters as F
from gpr_analyzer.processing.auto import ProcessingOptions, run_pipeline
from gpr_analyzer.processing.autodisplay import auto_display, choose_band, display_gain
from gpr_analyzer.synthetic import Cavity, make_scene, ricker


def _prep(rg):
    """Time-zeroed, dewowed data as run_pipeline hands it to the display chain."""
    d, dt = rg.data.astype(np.float32), rg.dt_ns
    fc = F.estimate_center_frequency(d, dt)
    d = F.dewow(d, dt, 2e3 / fc)
    picks = F.first_break_picks(d, 0.3, max(int(round(1e3 / fc / dt)), 2))
    return F.time_zero_correct(d, picks, int(round(np.median(picks)))), fc


def _params(steps, name):
    return next(p for s, p, _ in steps if s == name)


def test_analysis_and_other_tracks_unchanged_by_auto_display():
    rg, _ = make_scene(n_traces=200, n_samples=300, seed=3)
    with_auto = run_pipeline(rg)
    without = run_pipeline(rg, tracks={"analysis", "display", "layers", "envelope", "energy", "viewpoint"})
    assert with_auto.autodisplay is not None and without.autodisplay is None
    for track in ("analysis", "display", "layers", "envelope", "energy", "viewpoint"):
        np.testing.assert_array_equal(getattr(with_auto, track).data, getattr(without, track).data)
    assert with_auto.velocity_m_per_ns == without.velocity_m_per_ns
    assert with_auto.autodisplay.metadata["track"] == "autodisplay"
    assert with_auto.autodisplay.metadata["display_clip"] > 0
    auto_steps = [s for s in with_auto.log if s.step.startswith("display_")]
    assert {s.step for s in auto_steps} >= {"display_bandpass", "display_background", "display_gain", "display_contrast"}
    assert all(s.reason.startswith("display only") for s in auto_steps)
    # auto_process (CLI / report) is analysis only and does not pay for the display chain
    assert run_pipeline(rg, tracks={"analysis"}).autodisplay is None


def test_input_not_modified():
    rg, _ = make_scene(n_traces=120, n_samples=256)
    d, fc = _prep(rg)
    before = d.copy()
    auto_display(d, rg.dt_ns, rg.dx_m, fc)
    np.testing.assert_array_equal(d, before)


def test_band_follows_measured_noise():
    """Same reflections; strong white noise must narrow the band, and the wavelet's own band always stays."""
    quiet, _ = make_scene(n_traces=200, noise_std=0.002, seed=1)
    noisy, _ = make_scene(n_traces=200, noise_std=0.03, seed=1)
    bands = {}
    for name, rg in (("quiet", quiet), ("noisy", noisy)):
        d, fc = _prep(rg)
        _, steps, _ = auto_display(d, rg.dt_ns, rg.dx_m, fc)
        p = _params(steps, "display_bandpass")
        assert p["method"].startswith("signal")
        bands[name] = (p["low_mhz"], p["high_mhz"])
    nyq = 0.5e3 / quiet.dt_ns
    for lo, hi in bands.values():
        assert lo <= 0.6 * 250 and hi >= 1.4 * 250  # 250 MHz Ricker's main band is kept
        assert hi < 0.95 * nyq
    assert bands["noisy"][1] < bands["quiet"][1]


def test_band_falls_back_without_noise_window():
    rg, _ = make_scene(n_traces=100, n_samples=200, noise_std=0.0)
    d, fc = _prep(rg)
    lo, hi, p, why = choose_band(d, rg.dt_ns, fc, cap=d.shape[0] - 5, skip=10)
    assert p["method"].startswith("fixed") and (lo, hi) == pytest.approx((0.4 * fc, 2.0 * fc))
    assert "no noise-only part" in why


def _ringing_scene(step_m=None):
    rg, truth = make_scene(n_traces=555, seed=5, layers=[], targets=[],
                           cavities=[Cavity(x_start_m=8.0, x_end_m=15.5, depth_top_m=1.5, depth_bottom_m=2.5, amplitude=1.0)])
    if step_m is not None:  # ringing changes where the surface changes
        t = np.arange(rg.n_samples)[:, None] * rg.dt_ns
        x = np.arange(rg.n_traces)[None, :] * rg.dx_m
        rg.data[:] += (np.where(x < step_m, 0.8, -0.5) * ricker(t - 6.0 - 20.0, 230.0)).astype(np.float32)
    return rg, truth


def test_stationary_ringing_uses_widest_window_and_keeps_cavity_roof():
    rg, truth = _ringing_scene()
    d, fc = _prep(rg)
    out, steps, _ = auto_display(d, rg.dt_ns, rg.dx_m, fc)
    p = _params(steps, "display_background")
    assert p["window"] == "whole line"
    assert p["banding_after_pct"] < 5.0
    # the 7.5 m wide roof survives; ViewPoint's 1.9 m running mean flattens most of it
    cv = truth.cavities[0]
    roof = int(round((2 * cv.depth_top_m / truth.velocity_m_per_ns) / rg.dt_ns))
    inside = slice(int(10.0 / rg.dx_m), int(13.5 / rg.dx_m))
    rows = slice(roof - 6, roof + 7)
    bp = F.bandpass(d, rg.dt_ns, 0.4 * fc, 2 * fc)
    vp = F.background_removal(bp, 101, "mean")
    ref = F.background_removal(bp, None, "median")  # roof as a whole-line median sees it
    a_auto = np.abs(out[rows, inside]).max(axis=0).mean() / np.abs(out[rows]).max()
    a_vp = np.abs(vp[rows, inside]).max(axis=0).mean() / np.abs(vp[rows]).max()
    assert a_auto > 0.7 and a_auto > 2 * a_vp
    assert np.abs(ref[rows, inside]).max() > 0


def test_ringing_that_changes_along_the_line_gets_a_shorter_window():
    rg, _ = _ringing_scene(step_m=14.0)
    d, fc = _prep(rg)
    _, steps, _ = auto_display(d, rg.dt_ns, rg.dx_m, fc)
    p = _params(steps, "display_background")
    assert p["window"] != "whole line"
    assert p["banding_after_pct"] <= 5.0


def test_gain_is_one_curve_that_never_drops_with_depth():
    rg, _ = make_scene(n_traces=200, seed=2)
    d, fc = _prep(rg)
    g, cap, p = display_gain(d, rg.dt_ns, 60.0)
    assert g.ndim == 1 and g.size == d.shape[0]
    assert np.all(np.diff(g) >= -1e-6)
    assert np.all(g[cap:] == g[cap]) and 0 < p["max_gain_db"] <= 60.0
    # tight cap
    assert display_gain(d, rg.dt_ns, 6.0)[0].max() == pytest.approx(10 ** (6 / 20), rel=1e-4)


def test_noise_only_line_gets_no_gain():
    rng = np.random.default_rng(0)
    d = rng.standard_normal((400, 300)).astype(np.float32)
    g, cap, p = display_gain(d, 0.25, 60.0)
    assert p["max_gain_db"] < 3.0


def test_dead_traces_stay_blank():
    rg, _ = make_scene(n_traces=150, n_samples=256)
    d, fc = _prep(rg)
    d[:, [0, 70]] = 0.0
    out, steps, _ = auto_display(d, rg.dt_ns, rg.dx_m, fc)
    assert _params(steps, "display_qc")["dead_traces"] == 2
    assert not out[:, [0, 70]].any()


def test_header_time_zero_line_runs():
    rg, _ = make_scene(n_traces=120, n_samples=256)
    rg.metadata["zero_level_sample"] = int(round(6.0 / rg.dt_ns))
    res = run_pipeline(rg, ProcessingOptions(estimate_velocity=False))
    assert res.autodisplay.data.shape == res.analysis.data.shape
