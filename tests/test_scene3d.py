"""Survey-space geometry with known metre offsets and nonzero antenna separation."""
import base64
import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from gpr_analyzer.app import scene3d
from gpr_analyzer.core import Radargram, depth_from_time


def line(lid="L1", gps=None, separation=0.0):
    d = np.arange(240, dtype=np.float32).reshape(20, 12) - 120
    raw = Radargram(d, 0.5, 0.1, 0.12, {"gps": gps or []})
    a = raw.copy_with(metadata={"antenna_separation_m": separation})
    result = SimpleNamespace(analysis=a, envelope=a.copy_with(data=np.abs(d)), viewpoint=a,
                             velocity_source_code="user")
    return SimpleNamespace(line_id=lid, source_file=f"{lid}.iprb", raw=raw, result=result)


def job(tmp_path, lines):
    return SimpleNamespace(job_id="test", folder=tmp_path, lines=lines,
                           info=SimpleNamespace(project="Test survey"))


GPS = [{"trace": 2, "lat": 13.0, "lon": 100.0},
       {"trace": 5, "lat": 13.00005, "lon": 100.00005},
       {"trace": 9, "lat": 13.0001, "lon": 100.0}]


def test_curved_navigation_coverage_and_common_frame(tmp_path):
    a = line(gps=GPS)
    b = line("L2", gps=[{"trace": 0, "lat": 13.0001, "lon": 100.0},
                        {"trace": 11, "lat": 13.0, "lon": 100.0}])
    scene = scene3d.survey_scene(job(tmp_path, [a, b]))
    first, reverse = scene["lines"]
    # No GPS extrapolation beyond the first/last fix; preserve curved middle and acquisition order.
    assert first["trace_indices"] == list(range(2, 10))
    assert max(p[0] for p in first["xy_m"]) > 5
    assert reverse["xy_m"][0][1] > reverse["xy_m"][-1][1]
    assert first["xy_m"][-1] == reverse["xy_m"][0]
    assert first["latlon"][0] == [13, 100]
    assert scene["origin_wgs84"] == {"lat": 13.0, "lon": 100.0}


def test_geographic_projection_known_distance_and_dateline():
    points = scene3d.local_en(np.array([0, 0]), np.array([0, 0.001]), 0, 0)
    assert points[1, 0] == pytest.approx(111.3195, abs=0.001)
    assert points[1, 1] == 0
    across = scene3d.local_en(np.array([0]), np.array([-179.999]), 0, 179.999)
    assert across[0, 0] == pytest.approx(222.639, abs=0.01)


def test_depth_geometry_and_texture_do_not_mutate_analysis(tmp_path):
    ln = line(gps=GPS, separation=0.4)
    original = ln.result.analysis.data.copy()
    result = scene3d.survey_scene(job(tmp_path, [ln]), track="envelope")["lines"][0]
    assert result["depth_m"][-1] == pytest.approx(float(depth_from_time(9.5, 0.12, 0.4)), abs=1e-6)
    assert result["depth_m"][-1] != pytest.approx(9.5 * 0.12 / 2)
    image = Image.open(io.BytesIO(base64.b64decode(result["texture"].split(",")[1])))
    assert image.size == (8, 20)
    assert image.mode == "RGB"
    np.testing.assert_array_equal(original, ln.result.analysis.data)


def test_missing_and_bad_gps_are_explicit_and_never_arranged_automatically(tmp_path):
    invalid = [{"trace": 0, "lat": float("nan"), "lon": 1}, {"trace": 2, "lat": 91, "lon": 100},
               {"trace": -1, "lat": 13, "lon": 100}, {"trace": 0, "lat": 0, "lon": 0}]
    scene = scene3d.survey_scene(job(tmp_path, [line(gps=invalid), line("L2", gps=GPS[:1])]))
    assert scene["lines"] == []
    assert len(scene["excluded"]) == 2
    assert scene["origin_wgs84"] is None


def test_manual_placement_persists_and_frames_do_not_mix(tmp_path):
    j = job(tmp_path, [line(gps=GPS), line("L2")])
    scene3d.save_layout(j, {"lines": {"L2": {"start": [500000, 1500000], "end": [500010, 1500005]}}})
    local = scene3d.survey_scene(j, frame="local")
    assert [ln["line_id"] for ln in local["lines"]] == ["L2"]
    assert local["lines"][0]["xy_m"][-1] == [500010, 1500005]
    assert local["origin_wgs84"] is None
    assert local["lines"][0]["latlon"] is None
    assert scene3d.read_layout(job(tmp_path, []))["lines"]["L2"]["start"] == [500000, 1500000]
    gps = scene3d.survey_scene(j)
    assert [ln["line_id"] for ln in gps["lines"]] == ["L1"]


@pytest.mark.parametrize("value", [[], {}, {"start": [0, 0], "end": [0, 0]},
                                         {"start": [0, 0], "end": [float("inf"), 1]},
                                         {"start": [0, 0, 0], "end": [1, 1]}])
def test_invalid_layout_does_not_replace_saved_data(tmp_path, value):
    j = job(tmp_path, [line()])
    scene3d.save_layout(j, {"lines": {"L1": {"start": [0, 0], "end": [1, 1]}}})
    before = (tmp_path / "scene3d.json").read_bytes()
    with pytest.raises(ValueError):
        scene3d.save_layout(j, {"lines": {"L1": value}})
    assert (tmp_path / "scene3d.json").read_bytes() == before


def test_display_budget_and_out_of_local_extent(tmp_path):
    ln = line(gps=[{"trace": 0, "lat": 13, "lon": 100}, {"trace": 11, "lat": 14, "lon": 100}])
    scene = scene3d.survey_scene(job(tmp_path, [ln]))
    assert not scene["lines"] and "5 km" in scene["excluded"][0]["reason"]


def test_unknown_time_zero_does_not_invent_a_ground_surface(tmp_path):
    ln = line(gps=GPS)
    ln.options = SimpleNamespace(time_zero=False)
    scene = scene3d.survey_scene(job(tmp_path, [ln]))
    assert scene["lines"] == [] and "surface depth is unknown" in scene["excluded"][0]["reason"]
    # A raw header ZERO LEVEL can still anchor depth if automatic correction is disabled.
    ln.result.analysis.metadata["zero_level_sample"] = 3
    out = scene3d.survey_scene(job(tmp_path, [ln]))["lines"][0]
    assert out["sample_indices"][0] == 3 and out["depth_m"][0] == 0


def test_app_scene_and_layout_survive_restart(tmp_path):
    from fastapi.testclient import TestClient
    from gpr_analyzer.app.server import create_app

    client = TestClient(create_app(tmp_path / "jobs"))
    result = client.post("/api/demo")
    assert result.status_code == 200
    jid = result.json()["job_id"]
    url = f"/api/jobs/{jid}/scene3d"
    scene = client.get(url).json()
    assert len(scene["lines"]) == 2
    assert all(len(ln["xy_m"]) <= scene3d.MAX_COLUMNS and len(ln["depth_m"]) <= scene3d.MAX_ROWS for ln in scene["lines"])
    assert client.get(url + "?track=invalid").status_code == 400
    assert client.get(url + "?frame=local").json()["lines"] == []
    layout = {"lines": {"L1": {"start": [0, 0], "end": [20, 0]}}}
    assert client.put(url + "/layout", json=layout).status_code == 200
    client = TestClient(create_app(tmp_path / "jobs"))
    restored = client.get(url + "?frame=local").json()
    assert restored["lines"][0]["xy_m"][-1] == [20, 0]
    assert restored["excluded"][0]["line_id"] == "L2"
    assert client.put(url + "/layout", json={"lines": {"unknown": {}}}).status_code == 400
    assert client.get("/api/jobs/missing/scene3d").status_code == 404
    for resource in ["three.module.min.js", "three.core.min.js", "OrbitControls.js", "LICENSE.txt"]:
        assert client.get("/static/vendor/three/" + resource).status_code == 200
