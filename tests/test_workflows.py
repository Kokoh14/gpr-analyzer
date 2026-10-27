import copy
import json
from dataclasses import asdict

import numpy as np
import pytest

from gpr_analyzer.processing import ProcessingOptions, run_pipeline
from gpr_analyzer.processing.workflows import export_workflow, load_workflow, run_workflow
from gpr_analyzer.synthetic import make_scene


@pytest.mark.parametrize("custom", [False, True])
def test_exact_workflow_round_trip(custom):
    raw, _ = make_scene(n_traces=81, n_samples=160, time_window_ns=80, seed=19)
    opt = ProcessingOptions(estimate_velocity=False, background="none", time_zero_source="pick",
                            dewow_window_ns=9.123456, background_statistic="mean", gain_max_db=21.123456,
                            band_mhz=(80.123456, 400.654321)) if custom else ProcessingOptions()
    original = run_pipeline(raw, opt)
    document = json.dumps(export_workflow(original))
    replay = run_workflow(raw, document)
    assert original.parameter_table() == replay.parameter_table()
    for track in ("analysis", "display", "layers", "envelope", "energy", "viewpoint"):
        np.testing.assert_array_equal(getattr(original, track).data, getattr(replay, track).data)
    assert asdict(load_workflow(document)[0]) == asdict(opt)
    # Saved options are a snapshot, not a pointer that changes after the run.
    opt.gain_max_db = 1
    assert export_workflow(original)["options"]["gain_max_db"] != 1


def test_other_line_reuses_settings_and_history_is_audit_only():
    raw, _ = make_scene(n_traces=61, n_samples=160, time_window_ns=80, seed=3)
    other, _ = make_scene(n_traces=61, n_samples=160, time_window_ns=80, seed=4)
    result = run_pipeline(raw, ProcessingOptions(velocity_m_per_ns=.123456, dewow_window_ns=8.54321),
                          tracks={"analysis", "envelope"})
    doc = export_workflow(result)
    doc["history"] = [{"step": "not executable", "params": {"rounding": "ignored"}}]
    expected = run_pipeline(other, result.options, tracks={"analysis", "envelope"})
    actual = run_workflow(other, doc)
    np.testing.assert_array_equal(actual.analysis.data, expected.analysis.data)
    assert actual.parameter_table() == expected.parameter_table()


@pytest.mark.parametrize("key,value", [("gain_max_db", float("inf")), ("gain_max_db", -1),
                                       ("time_zero", "false"), ("estimate_velocity", None),
                                       ("background", "bogus"), ("time_zero_source", "bogus"),
                                       ("band_mhz", [100, 50]), ("velocity_m_per_ns", .5),
                                       ("typo", 2)])
def test_invalid_options_rejected(key, value):
    doc = {"schema": "gpr-analyzer.workflow", "version": 1, "tracks": ["analysis"], "history": [], "options": {key:value}}
    with pytest.raises(ValueError):
        load_workflow(doc)


def test_schema_and_tracks_rejected():
    for doc in ["broken", {}, {"schema":"gpr-analyzer.workflow", "version":2},
                {"schema":"gpr-analyzer.workflow", "version":1, "options":{}, "history":[], "tracks":["bogus"]}]:
        with pytest.raises(ValueError):
            load_workflow(doc)


def test_api_export_reapply_compare_and_checkpoint(tmp_path):
    from fastapi.testclient import TestClient
    from gpr_analyzer.app.server import create_app

    root = tmp_path / "jobs"
    app = create_app(root, background=False)
    c = TestClient(app)
    job = c.post("/api/demo3d").json()
    jid = job["job_id"]
    route = f"/api/jobs/{jid}/lines/L1"
    a = c.get(route + "/workflow.json").json()
    assert c.post("/api/workflows/validate", json=a).status_code == 200
    before = app.state.store.get(jid).line("L1").result.analysis.data.copy()
    steps = job["lines"][0]["steps"]
    assert c.post(route + "/reprocess", json={"options":a["options"]}).status_code == 200
    np.testing.assert_array_equal(before, app.state.store.get(jid).line("L1").result.analysis.data)
    assert c.get(f"/api/jobs/{jid}").json()["lines"][0]["steps"] == steps
    b = copy.deepcopy(a); b["options"].update(velocity_m_per_ns=.12, background="none", dewow_window_ns=7.1234)
    state = (root / jid / "state.json").read_bytes()
    result = c.post(route + "/workflows/compare", json={"left":a, "right":b})
    assert result.status_code == 200
    assert result.json()["left"]["png"] != result.json()["right"]["png"]
    assert (root / jid / "state.json").read_bytes() == state
    assert c.post(route + "/reprocess", json={"options":b["options"]}).status_code == 200
    c2 = TestClient(create_app(root, background=False))
    restored = c2.get(route + "/workflow.json").json()
    assert restored["options"]["dewow_window_ns"] == 7.1234
    assert restored["options"]["velocity_m_per_ns"] == .12
    assert c.post(route + "/workflows/compare", json={"left":{}, "right":b}).status_code == 400
    old_options = asdict(app.state.store.get(jid).line("L1").options)
    invalid = c.post(route + "/reprocess", json={"options":{"band_mhz":[10000, 20000]}})
    assert invalid.status_code == 400
    assert asdict(app.state.store.get(jid).line("L1").options) == old_options
