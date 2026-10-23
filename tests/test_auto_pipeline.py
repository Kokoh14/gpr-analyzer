import numpy as np
import pytest

import json

from gpr_analyzer.processing import auto_process
from gpr_analyzer.processing.auto import ProcessingOptions, run_pipeline
from gpr_analyzer.synthetic import Layer, PointTarget, make_scene

STEPS = [
    "center_frequency", "dewow", "time_zero", "bandpass", "background_removal",
    "gain_sec", "velocity", "envelope", "agc", "viewpoint_display",
]


@pytest.fixture(scope="module")
def scene():
    return make_scene()


@pytest.fixture(scope="module")
def result(scene):
    return run_pipeline(scene[0])


def test_log_records_every_step_with_reason(result):
    assert [s.step for s in result.log] == STEPS
    for row in result.parameter_table():
        assert row["reason"] and isinstance(row["params"], dict)


def test_data_driven_choices(result, scene):
    _, truth = scene
    p = {s.step: s.params for s in result.log}
    assert abs(p["center_frequency"]["center_freq_mhz"] - truth.freq_mhz) < 40
    assert p["bandpass"]["low_mhz"] < truth.freq_mhz < p["bandpass"]["high_mhz"]
    assert abs(p["time_zero"]["time_zero_ns"] - truth.time_zero_ns) < 0.5


def test_velocity_from_hyperbolas(result, scene):
    _, truth = scene
    assert result.velocity_m_per_ns == pytest.approx(truth.velocity_m_per_ns, rel=0.02)
    assert "hyperbola" in result.velocity_source
    assert len(result.hyperbola_fits) == 3
    for tg in truth.targets:
        f = min(result.hyperbola_fits, key=lambda f: abs(f.x0_m - tg.x_m))
        assert f.x0_m == pytest.approx(tg.x_m, abs=0.1)
        assert f.depth_m == pytest.approx(tg.depth_m, rel=0.03)
    assert result.analysis.velocity_m_per_ns == result.velocity_m_per_ns


@pytest.mark.parametrize("v", [0.06, 0.135])
def test_velocity_other_media(v):
    rg, _ = make_scene(velocity_m_per_ns=v, seed=2)
    r = run_pipeline(rg)
    assert r.velocity_m_per_ns == pytest.approx(v, rel=0.02)


def test_time_zero_puts_apex_at_true_time(result, scene):
    rg, truth = scene
    tg = truth.targets[0]
    j = truth.target_trace(tg, rg.dx_m)
    i = int(np.argmax(result.envelope.data[:, j]))
    assert abs(i * rg.dt_ns - truth.target_time_ns(tg)) < 1.0


def test_ringing_band_removed_targets_kept(result, scene):
    rg, truth = scene
    i_ring = int(round(truth.ringing_ns / rg.dt_ns))
    env = result.envelope.data
    quiet = slice(450, 555)  # away from targets and artifact traces
    band_before = np.abs(result.layers.data[i_ring - 3 : i_ring + 4, quiet]).max()
    band_after = env[i_ring - 3 : i_ring + 4, quiet].max()
    assert band_after < 0.1 * band_before
    tg = truth.targets[0]
    j = truth.target_trace(tg, rg.dx_m)
    i = int(round(truth.target_time_ns(tg) / rg.dt_ns))
    win = slice(i - 10, i + 11)
    assert env[win, j].max() > 0.8 * np.abs(result.layers.data[win, j]).max()


def test_layers_track_keeps_flat_reflector():
    rg, truth = make_scene(targets=[], layers=[Layer(depth_m=2.0, amplitude=0.5)], artifact_traces=[], ringing_ns=None)
    r = run_pipeline(rg)
    i = int(round(2 * 2.0 / truth.velocity_m_per_ns / rg.dt_ns))
    lay = np.abs(r.layers.data[i - 3 : i + 4]).max(axis=0)
    ana = np.abs(r.analysis.data[i - 3 : i + 4]).max(axis=0)
    assert np.median(lay) > 5 * np.median(ana)


def test_gain_held_constant_below_noise_floor(result):
    g = result.gain_curve
    cap = result.noise_floor_sample
    assert cap < g.size
    assert np.all(g[cap:] == g[cap])
    assert g[cap] >= g[: cap + 1].max() - 1e-9


def test_analysis_track_preserves_relative_amplitude():
    targets = [PointTarget(x_m=6.0, depth_m=2.0, amplitude=1.0), PointTarget(x_m=18.0, depth_m=2.0, amplitude=2.0)]
    rg, truth = make_scene(targets=targets, layers=[], artifact_traces=[], ringing_ns=None)
    r = run_pipeline(rg)
    a1 = r.envelope.data[:, 120].max()
    a2 = r.envelope.data[:, 360].max()
    assert a2 / a1 == pytest.approx(2.0, rel=0.1)


def test_user_overrides_are_respected_and_logged(scene):
    rg, _ = scene
    opt = ProcessingOptions(velocity_m_per_ns=0.12, band_mhz=(100.0, 450.0), background="global", migration="stolt")
    r = run_pipeline(rg, opt)
    p = {s.step: s for s in r.log}
    assert r.velocity_m_per_ns == 0.12 and r.velocity_source == "user"
    assert p["bandpass"].params["low_mhz"] == 100.0 and p["bandpass"].reason == "set by user"
    assert p["background_removal"].params["window_traces"] == "all"
    assert r.migrated is not None and r.migrated.data.shape == r.analysis.data.shape
    assert p["migration"].params["method"] == "stolt"


def test_no_hyperbolas_falls_back_to_default_velocity():
    rg, _ = make_scene(targets=[], artifact_traces=[])
    r = run_pipeline(rg)
    assert r.velocity_m_per_ns == 0.1
    assert r.velocity_source_code == "default"
    assert r.hyperbola_fits == []


def test_missing_trace_spacing():
    rg, _ = make_scene(n_traces=120, targets=[PointTarget(x_m=3.0, depth_m=1.0)], artifact_traces=[])
    rg = rg.copy_with(dx_m=None)
    r = run_pipeline(rg, ProcessingOptions(migration="kirchhoff"))
    assert r.migrated is None
    assert r.hyperbola_fits == []
    assert {s.step: s.params for s in r.log}["background_removal"]["window_traces"] == "all"


def test_raw_is_untouched(scene):
    rg, _ = scene
    before = rg.data.copy()
    run_pipeline(rg)
    assert np.array_equal(rg.data, before)


def test_auto_process_hook_for_report(scene):
    rg, _ = scene
    out, steps = auto_process(rg)
    assert out.n_traces == rg.n_traces and out.metadata["track"] == "analysis"
    assert out.metadata["velocity_source"] == "hyperbola_fit"
    assert out.metadata["processing"] == steps
    # The report hook requests the analysis track only; do not claim display/attribute steps ran.
    assert [s["name"] for s in steps] == STEPS[:-3]
    for s in steps:
        assert set(s) == {"name", "params", "auto"} and s["auto"] is True and s["params"]["reason"]
    json.dumps(steps)  # report serialises them


def test_user_choices_marked_not_auto(scene):
    rg, _ = scene
    _, steps = auto_process(rg, ProcessingOptions(velocity_m_per_ns=0.12, band_mhz=(100.0, 450.0)))
    auto = {s["name"]: s["auto"] for s in steps}
    assert auto["velocity"] is False and auto["bandpass"] is False and auto["dewow"] is True
    assert {s["name"]: s for s in steps}["velocity"]["params"]["velocity_m_per_ns"] == 0.12
