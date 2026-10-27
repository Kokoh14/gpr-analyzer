import base64
import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from gpr_analyzer.app.scene3d import save_layout
from gpr_analyzer.app.thick_slice import render, strongest_returns
from gpr_analyzer.core import Radargram, depth_from_time
from gpr_analyzer.processing import ProcessingOptions


def grid(tmp_path):
    lines = []
    for i in range(4):
        data = np.zeros((50, 11), dtype=np.float32)
        data[10, :5] = 10  # .5 m
        data[30, 5:] = 10  # 1.5 m
        rg = Radargram(data, 1., .2)
        lines.append(SimpleNamespace(line_id=f"L{i}", raw=rg, options=ProcessingOptions(), sections=[],
                                     result=SimpleNamespace(analysis=rg, envelope=rg, velocity_m_per_ns=.1)))
    j = SimpleNamespace(folder=tmp_path, lines=lines)
    save_layout(j, {"lines": {ln.line_id: {"start": [0, i], "end": [2, i]} for i, ln in enumerate(lines)}})
    return j


def pixels(r):
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(r["png"].split(",")[1]))))


def test_synthetic_grid_shallow_and_deep_in_one_view(tmp_path):
    j = grid(tmp_path)
    r = render(j, 0, 2, frame="local")
    rgba = pixels(r)
    # 0.5 m yellow-green, 1.5 m cyan-blue in the SAME image.
    np.testing.assert_array_equal(rgba[-1, 0], [128, 255, 0, 255])
    np.testing.assert_array_equal(rgba[-1, -1], [0, 128, 128, 255])
    assert r["display_only"] and r["cells"] == 44
    assert (rgba[3, :, 3] == 0).all()  # unsurveyed space stays transparent
    assert len(r["excluded"]) == 0


def test_range_contrast_crossings_and_zone_exclusion(tmp_path):
    j = grid(tmp_path)
    j.lines[0].sections = [{"trace_start": 0, "trace_end": 0}]
    j.lines[0].result.envelope.data[30, 6] = 1
    a = pixels(render(j, 0, 2, contrast_db=40, frame="local"))
    b = pixels(render(j, 0, 2, contrast_db=20, frame="local"))
    assert a[-1, 6, :3].sum() > b[-1, 6, :3].sum()
    assert a[-1, 0, 3] == 0
    only_shallow = pixels(render(j, 0, 1, frame="local"))
    assert only_shallow[-1, -1, 3] == 0
    # Crossing cells take the strongest amplitude, not line iteration order.
    save_layout(j, {"lines": {"L0": {"start": [0, 0], "end": [2, 0]},
                             "L1": {"start": [0, 0], "end": [2, 0]}}})
    j.lines[1].result.envelope.data[30, 1] = 100
    c = pixels(render(j, 0, 2, frame="local"))
    assert c[-1, 1, 2] > 0 and c[-1, 1, 0] == 0


def test_depth_uses_separation_and_invalid_parameters(tmp_path):
    data = np.zeros((40, 2)); data[20] = 7
    amp, depth = strongest_returns(data, 1, .1, .4, 0, 2)
    np.testing.assert_allclose(depth, depth_from_time(20, .1, .4))
    j = grid(tmp_path)
    for args in [(1, 0, 40), (0, float("nan"), 40), (0, 2, 0), (None, 2, 40)]:
        with pytest.raises(ValueError):
            render(j, *args, frame="local")
    with pytest.raises(ValueError, match="no positioned"):
        render(j, 0, 2, frame="gps")


def test_gps_gap_and_no_extrapolation(tmp_path):
    j = grid(tmp_path)
    ln = j.lines[0]
    ln.raw.metadata["gps"] = [dict(trace=t, lat=13, lon=100 + t / 100000) for t in (2, 4, 8)]
    r = render(j, 0, 2, frame="gps")
    assert r["cells"] == 7 and len(r["excluded"]) == 3
    ln.raw.dx_m = 2  # long gap from trace 4 to 8; only measured endpoints survive there
    r = render(j, 0, 2, frame="gps")
    assert r["cells"] == 4


def test_thick_slice_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from gpr_analyzer.app.server import create_app

    j = grid(tmp_path)
    app = create_app(tmp_path / "jobs", background=False)
    job = app.state.store.new()
    job.lines = j.lines
    save_layout(job, {"lines": {"L0": {"start": [0, 0], "end": [2, 0]}}})
    c = TestClient(app)
    route = f"/api/jobs/{job.job_id}/thick-slice"
    good = c.post(route, json={"top_m": 0, "bottom_m": 2, "frame": "local"})
    assert good.status_code == 200 and good.json()["display_only"]
    assert c.post(route, json={"top_m": 2, "bottom_m": 0}).status_code == 400
    assert c.post(route, json={}).status_code == 400
