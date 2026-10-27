"""Map panel data: GPS per trace, top-view energy slices, ViewPoint view, old reference-source names."""

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app import compare as cmp  # noqa: E402
from gpr_analyzer.app import geo  # noqa: E402
from gpr_analyzer.app.server import create_app  # noqa: E402


def test_trace_positions_interpolates_and_needs_two_fixes():
    gps = [{"trace": 0, "lat": 13.0, "lon": 100.0}, {"trace": 10, "lat": 13.001, "lon": 100.002},
           {"trace": 10, "lat": 99.0, "lon": 0.0}]  # duplicate trace number: first one kept
    lat, lon = geo.trace_positions(gps, 21)
    assert lat[5] == pytest.approx(13.0005) and lon[5] == pytest.approx(100.001)
    assert lat[20] == pytest.approx(13.001)  # held at the last fix
    assert geo.trace_positions(gps[:1], 21) is None
    assert geo.trace_positions([{"trace": 0, "lat": 0.0, "lon": 0.0}] * 3, 5) is None


def test_path_length_and_slices():
    lat = 13.0 + np.arange(101) * (1.0 / 111_195)  # ~1 m steps north
    assert geo.path_length_m(lat, np.full(101, 100.0)) == pytest.approx(100.0, rel=0.01)
    env = np.zeros((100, 3000))
    env[50, 1200] = 1.0
    sl = geo.energy_slices(env, dt_ns=0.5, velocity_m_per_ns=0.1, n_bands=10)
    assert len(sl["db"]) == 10 and len(sl["db"][0]) == len(sl["trace_groups"]) <= geo.MAX_MAP_TRACES
    band, group = 5, next(i for i, (a, b) in enumerate(sl["trace_groups"]) if a <= 1200 <= b)
    assert sl["db"][band][group] == 0 and sl["db"][0][group] == geo.SLICE_FLOOR_DB
    assert sl["depth_edges_m"][-1] == pytest.approx(100 * 0.5 * 0.1 / 2)


def test_demo_map_endpoints_and_coordinates(tmp_path):
    c = TestClient(create_app(tmp_path / "jobs", background=False))
    j = c.post("/api/demo").json()
    jid, ln = j["job_id"], j["lines"][0]
    assert ln["has_gps"] and "viewpoint" in ln["views"]
    m = c.get(f"/api/jobs/{jid}/lines/L1/map.json").json()
    assert m["has_gps"] and len(m["points"]) == ln["n_traces"]
    assert m["length_gps_m"] == pytest.approx(m["length_wheel_m"], rel=0.05)
    assert len(m["slice"]["db"]) == len(m["slice"]["depth_edges_m"]) - 1
    ov = c.get(f"/api/jobs/{jid}/map.json").json()
    assert [o["line_id"] for o in ov] == ["L1", "L2"] and all(o["has_gps"] for o in ov)
    r = c.get(f"/api/jobs/{jid}/lines/L1/viewpoint.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    csv = c.get(f"/api/jobs/{jid}/export/zones.csv").text
    assert "WGS84" in csv  # zones get start/end coordinates from the GPS track


def test_old_reference_sources_are_read_as_viewpoint():
    st = cmp.new_state()
    st["features"][0]["source"] = "subscan"
    st["lines"]["L1"] = {"meta": {"subscan": {"n_traces": 5}}, "picks": [{"source": "other"}],
                         "images": [{"source": "subscan"}], "verdicts": {}}
    cmp.upgrade_state(st)
    ls = st["lines"]["L1"]
    assert st["features"][0]["source"] == "viewpoint" and set(ls["meta"]) == {"viewpoint"}
    assert ls["picks"][0]["source"] == ls["images"][0]["source"] == "viewpoint"
    assert set(cmp.SOURCES) == {"viewpoint", "geolitix"}


def test_dielectric_points_from_hyperbola_fits(tmp_path):
    assert geo.dielectric(geo.LIGHT_M_PER_NS / 3) == pytest.approx(9.0)
    c = TestClient(create_app(tmp_path / "jobs", background=False))
    jid = c.post("/api/demo").json()["job_id"]
    d = {x["line_id"]: x for x in c.get(f"/api/jobs/{jid}/dielectric.json").json()}
    l1, l2 = d["L1"], d["L2"]
    assert l1["velocity_source"] == "default" and l1["points"] == []
    assert l2["velocity_source"] == "hyperbola_fit" and l2["has_gps"] and len(l2["points"]) == 2
    for p in l2["points"]:
        assert p["eps_r"] == pytest.approx(geo.dielectric(p["velocity_m_per_ns"]), abs=0.2)
        assert 0 <= p["trace"] < 2000 and abs(p["lat"]) <= 90 and p["depth_m"] > 0
    # a fit inside a section excluded from the results is left out
    t = l2["points"][0]["trace"]
    r = c.post(f"/api/jobs/{jid}/lines/L2/sections", json={"trace_start": t - 5, "trace_end": t + 5, "reason": "test"})
    assert r.status_code == 200
    l2 = next(x for x in c.get(f"/api/jobs/{jid}/dielectric.json").json() if x["line_id"] == "L2")
    assert len(l2["points"]) == 1


def test_gps_qa_flags_weak_fix_length_and_start_gap():
    step = 1.0 / 111_195  # ~1 m north per fix
    good = [{"trace": 10 * i, "lat": 13.0 + i * step, "lon": 100.0, "quality": "4"} for i in range(11)]
    q = geo.gps_qa(good, 101, 0.1)
    assert q["flags"] == [] and q["length_ratio"] == pytest.approx(1.0, abs=0.02) and q["weak_fix_share"] == 0
    # late first fix, non-RTK jitter sideways that makes the GPS path longer than the wheel
    bad = [dict(r, trace=r["trace"] + 5, quality="1", lon=100.0 + (i % 2) * 1e-5) for i, r in enumerate(good[:-1])]
    q = geo.gps_qa(bad, 101, 0.1)
    assert {"weak_fix", "length_mismatch", "gap"} <= set(q["flags"]) and q["start_gap_m"] == pytest.approx(0.5)
    assert geo.gps_qa(good[:1], 101, 0.1) == {"fixes": 1, "flags": []}
    assert geo.fix_quality(bad, [0, 5, 14, 15, 100]) == ["1"] * 5
    assert geo.fix_quality(good[:3] + [dict(good[3], quality="5")], [0, 25, 30, 40]) == ["4", "4", "5", "5"]


def test_overview_numbers_lines_in_recording_order(tmp_path):
    c = TestClient(create_app(tmp_path / "jobs", background=False))
    jid = c.post("/api/demo").json()["job_id"]
    ov = c.get(f"/api/jobs/{jid}/map.json").json()
    assert sorted(o["order"] for o in ov) == [1, 2]
    for o in ov:
        assert len(o["quality"]) == len(o["points"]) == len(o["traces"]) and o["qa"]["fixes"] >= 2
