"""IDS GPS lag measured on structures (bridges) and applied to a line's fixes and km."""

import time

import numpy as np
import pytest
from test_ids import make_zone

from gpr_analyzer.app import gps_lag
from gpr_analyzer.io.ids import read_ids

DX = 0.36
KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder>{}</Folder></Document></kml>"""
PM = "<Placemark><name>{n}</name><Point><coordinates>{lon},{lat},0</coordinates></Point></Placemark>"


def track(n=3000, lat0=16.0, lon0=100.0):
    """A line due north, one trace every DX m."""
    return lat0 + np.arange(n) * DX / 110540, np.full(n, lon0)


def test_lag_is_median_of_bridge_to_surface_change(tmp_path):
    lat, lon = track()
    bridges = [500, 1200, 1900, 2600]
    spans = [(b - 100 - 10, b - 100 + 10) for b in bridges] + [(2900, 2950)]  # radar sees each 100 traces earlier
    pts = np.array([(lat[b], lon[b] + 10 / 107000) for b in bridges] + [(lat[1500], lon[1500] + 0.01)])  # last: far off
    pts = np.vstack([pts, pts[:1]])  # the same bridge twice (twin tracks) counts once
    assert gps_lag.estimate_lag(lat, lon, spans, pts, DX) == (100, 4)
    assert gps_lag.estimate_lag(lat, lon, spans[:2], pts[:2], DX) is None  # too few to trust
    assert gps_lag.estimate_lag(lat, lon, [(b - 300, b - 290) for b in bridges], pts, DX) is None  # beyond 60 m
    rows = [{"trace": 0, "lat": 1.0}, {"trace": 50, "lat": 2.0}]
    assert gps_lag.shift(rows, 30) == [{"trace": -30, "lat": 1.0}, {"trace": 20, "lat": 2.0}]

    f = tmp_path / "bridges.kml"
    f.write_text(KML.format(PM.format(n="a", lon=100.5, lat=16.25) + "<Placemark><LineString><coordinates>1,2 3,4"
                            "</coordinates></LineString></Placemark>"), encoding="utf-8")
    assert gps_lag.read_points(f).tolist() == [[16.25, 100.5]]


def test_read_ids_one_channel(tmp_path):
    z = make_zone(tmp_path / "J.MIS" / "North_ob.ZON")
    rgs = read_ids(z / "LI010001.dt", channels={1})
    assert [r.metadata["channel"] for r in rgs] == [1]


def test_job_shifts_ids_fixes(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient

    from gpr_analyzer.app.server import create_app

    folder = tmp_path / "JOB.MIS"
    make_zone(folder / "North_2221to2239_ob.ZON", parts=(300, 200))
    kml = tmp_path / "bridges.kml"
    kml.write_text(KML.format(PM.format(n="a", lon=100.5, lat=16.25)), encoding="utf-8")
    calls = []

    def fake(primary, points):
        calls.append(primary)
        gps = read_ids(primary, channels={1})[0].metadata["gps"]
        return 20, 5, gps_lag.shift(gps, 20)

    monkeypatch.setattr(gps_lag, "zone_lag", fake)
    app = create_app(tmp_path / "jobs")
    c = TestClient(app)
    body = {"path": str(folder), "recursive": True,
            "ranges": {str(folder / "North_2221to2239_ob.ZON" / "LI010001.dt"): [36, 144]}}
    assert c.post("/api/jobs/from-folder", json={**body, "structures_kml": str(tmp_path / "nope.kml")}).status_code == 400
    jid = c.post("/api/jobs/from-folder", json={**body, "structures_kml": str(kml)}).json()["job_id"]
    t = time.monotonic()
    while c.get(f"/api/jobs/{jid}/progress").json()["state"] != "done":
        assert time.monotonic() - t < 120
        time.sleep(0.2)
    assert len(calls) == 1  # measured once for the zone, used by all three channels
    assert "เลื่อนตำแหน่ง GPS กลับ 7.2 ม. (วัดจากสะพาน 5 แห่ง)" in c.get(f"/api/jobs/{jid}/log.txt").text
    plain = {g["trace"]: g["lat"] for g in read_ids(folder / "North_2221to2239_ob.ZON" / "LI010001.dt", channels={1})[0].metadata["gps"]}
    for ln in app.state.store.get(jid).lines:
        assert ln.trace_offset == 100 and ln.raw.metadata["gps_lag_traces"] == 20
        gps = ln.raw.metadata["gps"]
        assert gps[0]["trace"] < 0 and gps[-1]["trace"] > ln.raw.n_traces - 1  # anchors past both ends kept
        assert all(plain[g["trace"] + 20 + 100] == g["lat"] for g in gps)  # each fix now 20 traces earlier
