"""3D mode 2: gridded volume with measured / interpolated / empty cells, and zones in 3D."""
import base64
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from gpr_analyzer.app import scene3d, volume3d
from gpr_analyzer.core import Radargram, depth_from_time
from gpr_analyzer.report.schema import Zone

DT, V = 0.5, 0.1


def line(lid, n=21, spike_row=40, separation=0.0, velocity=V, gps=None, amp=10.0, dx=0.1):
    d = np.full((120, n), 0.01, dtype=np.float32)
    d[spike_row] = amp
    a = Radargram(d, DT, dx, velocity, {"antenna_separation_m": separation, "gps": gps or []})
    result = SimpleNamespace(analysis=a, envelope=a, velocity_m_per_ns=velocity, velocity_source_code="user")
    return SimpleNamespace(line_id=lid, source_file=f"{lid}.iprb", raw=a, result=result, sections=[], zones=[])


def job(tmp_path, lines, layout=None):
    j = SimpleNamespace(job_id="t", folder=tmp_path, lines=lines, info=SimpleNamespace(project="Test"))
    if layout:
        scene3d.save_layout(j, {"lines": layout})
    return j


def decode(v):
    g = v["grid"]
    vol = np.frombuffer(base64.b64decode(v["volume_b64"]), np.uint8).reshape(g["nz"], g["ny"], g["nx"])
    cells = np.frombuffer(base64.b64decode(v["cells_b64"]), np.uint8).reshape(g["ny"], g["nx"])
    return vol, cells, np.array(g["depth_m"])


def parallel(tmp_path, gap=0.4, **kw):
    lines = [line("A", **kw), line("B", **kw)]
    return job(tmp_path, lines, {"A": {"start": [0.05, 0.05], "end": [2.05, 0.05]},
                                 "B": {"start": [0.05, 0.05 + gap], "end": [2.05, 0.05 + gap]}})


def test_depth_uses_each_lines_velocity_and_separation(tmp_path):
    lines = [line("A", separation=0.4), line("B", velocity=0.08)]
    j = job(tmp_path, lines, {"A": {"start": [0, 0], "end": [2, 0]}, "B": {"start": [0, 3], "end": [2, 3]}})
    v = volume3d.survey_volume(j, frame="local", dz_m=0.01)
    vol, cells, z = decode(v)
    for row, true in ((0, depth_from_time(40 * DT, V, 0.4)), (-1, depth_from_time(40 * DT, 0.08))):
        assert z[np.argmax(vol[:, row, 5])] == pytest.approx(float(true), abs=0.005)
    assert depth_from_time(40 * DT, V, 0.4) != pytest.approx(40 * DT * V / 2, abs=0.005)
    # The peak is near 0 dB of the line reference (the depth grid misses the spike slightly); the background
    # is clipped to the floor; below the deepest sample of line A there is no data.
    assert vol[:, 0, 5].max() > 240 and vol[:, 0, 5].min() == 0 and np.median(vol[:, 0, 5]) == 1


def test_interpolation_only_between_lines_within_max_gap(tmp_path):
    v = volume3d.survey_volume(parallel(tmp_path), frame="local", max_gap_m=0.5)
    vol, cells, z = decode(v)
    assert v["grid"]["ny"] == 5
    assert (cells[0] == 2).all() and (cells[4] == 2).all()
    assert (cells[1:4] == 1).all()  # 0.4 m gap fully bridged
    # Interpolated cells repeat the identical measured reflector at the same depth.
    np.testing.assert_array_equal(vol[:, 2, 10], vol[:, 0, 10])
    assert v["counts"] == {"measured": 42, "interpolated": 63, "empty": 0}
    tight = volume3d.survey_volume(parallel(tmp_path), frame="local", max_gap_m=0.3)
    assert (decode(tight)[1][1:4] == 0).all() and tight["counts"]["interpolated"] == 0


def test_no_extrapolation_beyond_lines_or_line_ends(tmp_path):
    lines = [line("A"), line("B"), line("C", n=5)]
    j = job(tmp_path, lines, {"A": {"start": [0.05, 0.05], "end": [2.05, 0.05]},
                              "B": {"start": [0.05, 0.45], "end": [2.05, 0.45]},
                              "C": {"start": [0.05, 1.25], "end": [0.45, 1.25]}})
    v = volume3d.survey_volume(j, frame="local", max_gap_m=0.5)
    _, cells, _ = decode(v)
    assert (cells[5:12] == 0).all()  # 0.8 m from B to C exceeds the max gap
    assert (cells[12, 5:] == 0).all() and (cells[12, :5] == 2).all()  # C stops at 0.45 m
    single = volume3d.survey_volume(job(tmp_path, [line("A")], {"A": {"start": [0, 0], "end": [2, 1]}}),
                                    frame="local", max_gap_m=2)
    assert single["counts"]["interpolated"] == 0  # one line never brackets a cell


def test_interpolated_value_weights_nearer_line(tmp_path):
    j = parallel(tmp_path)
    j.lines[1].result.envelope.data[40] = 1.0  # B is 20 dB weaker, each line normalised to itself...
    j.lines[1].result.envelope.data[60] = 100.0  # ...so B's own peak sits deeper
    vol, cells, z = decode(volume3d.survey_volume(j, frame="local", max_gap_m=0.5))
    a_row, b_row = int(np.argmin(np.abs(z - depth_from_time(40 * DT, V)))), int(np.argmin(np.abs(z - depth_from_time(60 * DT, V))))
    near_a, near_b = vol[:, 1, 10].astype(int), vol[:, 3, 10].astype(int)
    assert near_a[a_row] > near_b[a_row] and near_b[b_row] > near_a[b_row]


def test_sections_and_gps_gaps_stay_empty(tmp_path):
    j = parallel(tmp_path)
    j.lines[0].sections = [{"trace_start": 0, "trace_end": 9}]
    _, cells, _ = decode(volume3d.survey_volume(j, frame="local", max_gap_m=0.5))
    assert (cells[0, :10] == 0).all() and (cells[0, 11:] == 2).all()
    # Two fixes 20 traces apart at dx 0.5 m = 10 m without GPS: only the fixes themselves are placed.
    gps = [{"trace": 0, "lat": 13.0, "lon": 100.0}, {"trace": 20, "lat": 13.0, "lon": 100.0001}]
    ln = line("G", gps=gps, dx=0.5)
    xy, reason = volume3d.positions(ln, "gps", {"lat": 13.0, "lon": 100.0}, {"lines": {}})
    assert reason is None and np.isfinite(xy).all(axis=1).tolist() == [True] + [False] * 19 + [True]
    ln = line("G", gps=gps, dx=0.1)
    xy, _ = volume3d.positions(ln, "gps", {"lat": 13.0, "lon": 100.0}, {"lines": {}})
    assert np.isfinite(xy).all() and xy[-1, 0] == pytest.approx(10.85, abs=0.01)


def test_invalid_parameters_and_size_limit(tmp_path):
    j = parallel(tmp_path)
    for kw in ({"cell_m": 0}, {"dz_m": float("nan")}, {"max_gap_m": -1}, {"frame": "utm"}):
        with pytest.raises(ValueError):
            volume3d.survey_volume(j, **{"frame": "local", **kw})
    with pytest.raises(ValueError, match="no positioned lines"):
        volume3d.survey_volume(j, frame="gps")
    big = job(tmp_path, [line("A")], {"A": {"start": [0, 0], "end": [9000, 9000]}})
    with pytest.raises(ValueError, match="too large"):
        volume3d.survey_volume(big, frame="local")


def zone(lid, zid, review="auto", t0=0, t1=9):
    return Zone(zone_id=zid, line_id=lid, trace_start=t0, trace_end=t1, time_top_ns=10, time_bottom_ns=20,
                velocity_m_per_ns=V, velocity_source="user", suspected_type="void_like", confidence="medium",
                review=review, antenna_separation_m=0.4)


def test_zones_in_survey_frame(tmp_path):
    j = parallel(tmp_path)
    j.lines[0].zones = [zone("A", "Z1", "accepted", 5, 9), zone("A", "Z2", "rejected")]
    j.lines[1].zones = [zone("B", "Z3")]
    j.lines[1].sections = [{"trace_start": 0, "trace_end": 20}]
    out = volume3d.survey_zones(j, frame="local")
    assert [z["zone_id"] for z in out["zones"]] == ["Z1", "Z2"]
    z1 = out["zones"][0]
    assert z1["path_m"][0] == pytest.approx([0.55, 0.05]) and z1["path_m"][-1] == pytest.approx([0.95, 0.05])
    assert z1["depth_top_m"] == pytest.approx(float(depth_from_time(10, V, 0.4)), abs=1e-4)
    assert z1["review"] == "accepted" and out["unplaced"][0]["zone_id"] == "Z3"


def test_api(tmp_path):
    from fastapi.testclient import TestClient

    from gpr_analyzer.app.server import create_app

    app = create_app(tmp_path / "jobs", background=False)
    j = app.state.store.new()
    j.lines = parallel(j.folder).lines
    scene3d.save_layout(j, {"lines": {"A": {"start": [0, 0], "end": [2, 0]}}})
    c = TestClient(app)
    ok = c.get(f"/api/jobs/{j.job_id}/volume3d", params={"frame": "local"})
    assert ok.status_code == 200 and ok.json()["excluded"][0]["line_id"] == "B"
    assert c.get(f"/api/jobs/{j.job_id}/volume3d", params={"frame": "local", "cell_m": 9}).status_code == 400
    assert c.get(f"/api/jobs/{j.job_id}/zones3d", params={"frame": "local"}).status_code == 200


def test_synthetic_volume_demo_recovers_pipe_and_cavity(tmp_path):
    """End to end from written ImpulseRadar files: positions from .cor, depths from processing."""
    from fastapi.testclient import TestClient

    from gpr_analyzer.app.server import create_app

    c = TestClient(create_app(tmp_path / "jobs", background=False))
    jid = c.post("/api/demo-volume").json()["job_id"]
    v = c.get(f"/api/jobs/{jid}/volume3d", params={"max_gap_m": 0.6}).json()
    vol, cells, z = decode(v)
    g = v["grid"]
    assert not v["excluded"] and v["counts"]["empty"] < 0.05 * g["nx"] * g["ny"]

    def peak(x_m, y_m, top, bottom):
        ix, iy = int((x_m - g["x0_m"]) / g["cell_m"]), int((y_m - g["y0_m"]) / g["cell_m"])
        rows = (z >= top) & (z <= bottom)
        col = vol[rows, iy, ix]
        return z[rows][np.argmax(col)], int(col.max())

    # The pipe crosses every pass at 6 m along the line and 1.0 m deep, including between passes.
    for y in np.arange(0.0, 4.01, 0.25):
        depth, level = peak(6.0, y, 0.5, 1.5)
        assert depth == pytest.approx(1.0, abs=0.1) and level > 150  # within 16 dB of each line reference
    # The cavity roof (1.2 m) exists under passes 4-6 (1.5-2.5 m north), not under pass 1.
    assert peak(11.0, 2.0, 0.9, 1.5)[0] == pytest.approx(1.2, abs=0.1)
    assert peak(11.0, 2.0, 0.9, 1.5)[1] > peak(11.0, 0.0, 0.9, 1.5)[1] + 60
