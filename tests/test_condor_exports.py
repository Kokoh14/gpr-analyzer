import io
import json
import zipfile

import pytest

from gpr_analyzer.report import write_report
from gpr_analyzer.report.exports import condor_zip, zones_condor
from gpr_analyzer.report.schema import LineResult, SurveyInfo, SurveyResult, Zone


def zone(**changes):
    values = dict(zone_id="A", line_id="L1", trace_start=10, trace_end=20,
                  time_top_ns=10., time_bottom_ns=30., velocity_m_per_ns=.1, velocity_source="user",
                  suspected_type="void_like", confidence="high", review="accepted",
                  coord_start=(100., 13.), coord_end=(100.002, 13.002), datum="WGS84",
                  surface_elevation_start_m=10., surface_elevation_end_m=12.)
    return Zone(**{**values, **changes})


def survey(zones):
    return SurveyResult(info=SurveyInfo(project="Test"), lines=[LineResult(
        line_id="L1", source_file="synthetic", n_traces=30, n_samples=50, dt_ns=1., trace_spacing_m=.1,
        velocity_m_per_ns=.15, velocity_source="default", zones=zones)])


def test_exact_appendix_a_layout():
    """Appendix A: CNF v1 seven TSV fields; object v2 header and ---/FCODE NAME/XYZ blocks."""
    s = survey([zone()])
    before = s.to_json()
    bundle = zones_condor(s)
    assert bundle["cnf"] == (
        "VERSION:1\n"
        "2\t2\tGPR_void_like_high_L\tE03131\t25CF\t2\t0.5\n"
        "1\t2\tGPR_void_like_high_P\tE03131\t25CF\t2\t0.5\n")
    assert bundle["pobj"] == (
        "VERSION:2\nCOORDINATE TYPE:GPS\n---\nGPR_void_like_high_P\tL1:A [accepted]\n"
        "100.001000000\t13.001000000\t10.000000\n")
    assert bundle["lobj"] == (
        "VERSION:2\nCOORDINATE TYPE:GPS\n---\nGPR_void_like_high_L\tL1:A [accepted]\n"
        "100.000000000\t13.000000000\t9.000000\n"
        "100.002000000\t13.002000000\t11.000000\n")
    assert s.to_json() == before


def test_own_velocity_separation_and_missing_coordinates():
    s = survey([zone(), zone(zone_id="B", velocity_m_per_ns=.2),
                zone(zone_id="C", coord_start=None), zone(zone_id="D", surface_elevation_end_m=None),
                zone(zone_id="E", review="rejected")])
    # Line velocity .15 must never replace either zone's stored .1/.2.
    b = zones_condor(s)
    assert "100.001000000\t13.001000000\t9.000000" in b["pobj"]
    assert [x["target_depth_m"] for x in b["manifest"]["exported"]] == [1, 2]
    assert len(b["manifest"]["skipped"]) == 3
    s.lines[0].zones[0].antenna_separation_m = .4
    assert zones_condor(s)["manifest"]["exported"][0]["target_depth_m"] > 1
    with pytest.raises(ValueError, match="altitude missing"):
        zones_condor(survey([zone(surface_elevation_start_m=None)]))


def test_cartesian_point_only_and_mixed_systems():
    s = survey([zone(datum="Local metres", coord_end=None, surface_elevation_start_m=None)])
    b = zones_condor(s)
    assert b["lobj"] == "VERSION:2\nCOORDINATE TYPE:CARTESIAN\n"
    assert "100.000000000\t13.000000000\t-1.000000" in b["pobj"]
    assert "relative surface" in b["manifest"]["z_reference"]
    with pytest.raises(ValueError, match="cannot mix"):
        zones_condor(survey([zone(), zone(zone_id="B", datum="EPSG:32647")]))
    with pytest.raises(ValueError, match="single declared"):
        zones_condor(survey([zone(datum="EPSG:32647"), zone(zone_id="B", datum="EPSG:32648")]))
    with pytest.raises(ValueError, match="coordinate system"):
        zones_condor(survey([zone(datum=None)]))


def test_utf8_sanitized_names_and_zip(tmp_path):
    s = survey([zone(zone_id="ท่อ\tA\n", line_id="แนว1")])
    b = zones_condor(s)
    assert "แนว1:ท่อ A  [accepted]" in b["pobj"]
    data = condor_zip(s, tmp_path / "zones.zip").read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {"zones.cnf", "zones.pobj", "zones.lobj", "manifest.json"}
        assert archive.read("zones.pobj").decode("utf-8") == b["pobj"]
        assert json.loads(archive.read("manifest.json"))["exported"][0]["velocity_m_per_ns"] == .1
    for ext in ("cnf", "pobj", "lobj"):
        p = write_report(s, tmp_path / f"zones.{ext}")
        assert p.read_bytes() == b[ext].encode("utf-8")


def test_app_exports_matched_bundle_and_missing_altitude(tmp_path):
    from fastapi.testclient import TestClient
    from gpr_analyzer.app.server import create_app

    app = create_app(tmp_path / "jobs", background=False)
    c = TestClient(app)
    job = c.post("/api/demo3d").json()
    route = f"/api/jobs/{job['job_id']}/export/"
    response = c.get(route + "zones-condor.zip")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert z.read("zones.pobj").startswith(b"VERSION:2\nCOORDINATE TYPE:GPS\n")
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["exported"] and not manifest["skipped"]
    for ext in ("cnf", "pobj", "lobj"):
        assert c.get(route + f"zones.{ext}").status_code == 200
    # Missing altitudes never become invented GPS Z=0 or negative depth.
    for ln in app.state.store.get(job["job_id"]).lines:
        for g in ln.raw.metadata["gps"]:
            g.pop("elevation", None)
    assert c.get(route + "zones-condor.zip").status_code == 400
