"""Railway km from a reference KML and processing only chosen sections (risk points) of the lines."""

import time

import pytest

from gpr_analyzer.app import chainage
from test_ids import make_zone

LON = 100 + 35.53225 / 60  # the synthetic zone runs due north along this meridian from lat 13.9
KM_PER_DEG = 110.54


def lat_of(km):
    return 13.89 + (km - 10.0) / KM_PER_DEG


def write_kml(path, sections):
    def pm(data, k0, k1):
        ext = "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in data.items())
        return (f"<Placemark><ExtendedData>{ext}<Data name=\"กม_เริ่มต้น\"><value>{k0}</value></Data>"
                f"<Data name=\"กม_สิ้นสุด\"><value>{k1}</value></Data></ExtendedData><LineString><coordinates>"
                f"{LON},{lat_of(k0)},0 {LON},{lat_of(k1)},0</coordinates></LineString></Placemark>")
    centre = "".join(pm({}, k, k + 1) for k in (10, 11, 12))
    secs = 2 * "".join(pm({"ประเภทพื้นที่": "อัดหินบ่อย", "ลำดับ": s, "ชื่อพื้นที่": f"จุด {s}", "ประเภททาง": "ทางเดี่ยว"},
                      k0, k1) for s, k0, k1 in sections)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
                    f"<Folder><name>01_แนว</name>{centre}</Folder><Folder><name>05_จุดเสี่ยง</name>{secs}</Folder>"
                    "</Document></kml>", encoding="utf-8")
    return path


@pytest.fixture()
def survey(tmp_path):
    make_zone(tmp_path / "JOB.MIS" / "North_11105to11285_ob.ZON", parts=(300, 200))
    kml = write_kml(tmp_path / "tor.kml", [(1, 11.15, 11.20), (2, 11.27, 11.28), (3, 50.0, 51.0)])
    return tmp_path / "JOB.MIS", kml


def test_km_of_points_and_format(survey):
    ref, secs = chainage.read_kml(survey[1])
    km, d = ref.km_at([lat_of(10.5), lat_of(12.25)], [LON, LON + 0.0003])
    assert km == pytest.approx([10.5, 12.25], abs=0.011) and d[0] < 1 and 25 < d[1] < 40
    assert [s["id"] for s in secs] == ["อัดหินบ่อย-1", "อัดหินบ่อย-2", "อัดหินบ่อย-3"]
    assert secs[0]["name"] == "จุด 1" and secs[0]["km0"] == 11.15 and secs[0]["track"] == "ทางเดี่ยว"
    assert chainage.fmt_km(36.1) == "36+100" and chainage.fmt_km(487.5214) == "487+521"


def test_sections_matched_to_line_windows(survey):
    m = chainage.match_folder(survey[0], survey[1], recursive=True, buffer_km=0.01)
    by = {s["seq"]: s for s in m["sections"]}
    assert by[1]["lines"][0]["cover"] == 1.0 and by[2]["lines"] and not by[3]["lines"]
    (ln,) = m["lines"]
    w1, w2 = ln["windows"]  # two windows: the sections are too far apart to merge
    assert w1["sections"] == ["อัดหินบ่อย-1"] and w2["sections"] == ["อัดหินบ่อย-2"]
    assert w1["km_from"] < 11.15 and w1["km_to"] > 11.20 and w1["trace1"] < w2["trace0"]
    assert w1["range_m"][0] == pytest.approx(w1["trace0"] * 0.36, abs=0.2)
    assert chainage.km_at_trace(w1["chainage"], (w1["trace0"] + w1["trace1"]) / 2) == pytest.approx(11.175, abs=0.03)
    end = w2["chainage"][-1][0]  # the line's last traces lie after its last fix: the end slope is extended
    assert chainage.km_at_trace(w2["chainage"], end + 20) > chainage.km_at_trace(w2["chainage"], end)
    assert chainage.km_at_trace(w2["chainage"], end + 500) is None
    # one section only: one window
    one = chainage.match_folder(survey[0], survey[1], recursive=True, chosen={"อัดหินบ่อย-2"}, buffer_km=0.01)
    assert [w["sections"] for w in one["lines"][0]["windows"]] == [["อัดหินบ่อย-2"]]


def test_bad_kml_is_a_clear_error(survey, tmp_path):
    (tmp_path / "x.kml").write_text("<kml>", encoding="utf-8")
    with pytest.raises(ValueError, match="KML"):
        chainage.match_folder(survey[0], tmp_path / "x.kml", recursive=True)
    with pytest.raises(ValueError, match="KML"):
        chainage.match_folder(survey[0], tmp_path / "missing.kml", recursive=True)


def test_app_processes_only_chosen_sections_in_km(survey):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient

    from gpr_analyzer.app.server import create_app

    folder, kml = survey
    c = TestClient(create_app(folder.parent / "jobs"))
    r = c.post("/api/sections", json={"path": str(folder), "recursive": True, "kml": str(kml), "buffer_km": 0.01})
    assert r.status_code == 200 and len(r.json()["sections"]) == 3
    assert c.post("/api/sections", json={"path": str(folder), "kml": str(folder / "no.kml")}).status_code == 400
    jid = c.post("/api/jobs/from-folder", json={
        "path": str(folder), "recursive": True,
        "sections": {"kml": str(kml), "ids": ["อัดหินบ่อย-1"], "buffer_km": 0.01}}).json()["job_id"]
    t = time.monotonic()
    while c.get(f"/api/jobs/{jid}/progress").json()["state"] != "done":
        assert time.monotonic() - t < 120
        time.sleep(0.2)
    j = c.get(f"/api/jobs/{jid}").json()
    assert [x["line_id"] for x in j["lines"]] == ["L1-ch1", "L1-ch2", "L1-ch3"]
    ln = j["lines"][0]
    assert ln["trace_offset"] > 0 and ln["n_traces"] < 500
    assert ln["km_from"] < 11.15 < 11.20 < ln["km_to"] and ln["metadata"]["sections"] == "อัดหินบ่อย-1"
    assert all(z["km_start"] is None or 11.0 < z["km_start"] < 11.3 for z in ln["zones"])
    assert "กม. 11+" in c.get(f"/api/jobs/{jid}/log.txt").text
