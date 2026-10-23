"""Google Earth KML/KMZ in ViewPoint's layout: writing, reading, marker depth, export and answer-key import."""

import io
import os
import zipfile
from pathlib import Path

import numpy as np
import pytest

from gpr_analyzer.app import kml
from gpr_analyzer.core import Radargram
from gpr_analyzer.io.impulseradar import read_cor, read_mrk, read_impulseradar


def _profile():
    return kml.Profile(name="SITE_2026-10-22_0057", track=[(13.560253809, 100.6113348308), (13.5602582173, 100.6113325707)],
                       marks=[kml.Mark(name="Mark: 57.1", lat=13.5602683332, lon=100.6113273667, standard="APWA", index=2,
                                       alt=-27.52, depth_m=0.36, status="4", image="AutoScreenShots/57.1.jpg",
                                       type_name="Red", information=kml.APWA[2][1])])


def test_layout_matches_viewpoint_and_reads_back():
    text = kml.build_kml([_profile()])
    assert text.startswith("<?xml version='1.0' encoding='UTF-8' ?>\r\n<kml>\r\n  <Document>\r\n")
    assert text.endswith("</kml>") and text.count('<Style id="') == 8 + 10 + 6 + 1
    # coordinates: lon,lat,0 separated by LF only, numbers in shortest form
    assert "<coordinates>100.6113348308,13.560253809,0\n100.6113325707,13.5602582173,0\n</coordinates>\r\n" in text
    assert ("<h3>Type: Red</h3><h3>Information: Electric Power Lines, Cables, Conduit and Lighting Cables</h3>"
            "<h3>Standard: APWA</h3><h3>Lat: 13.5602683332 Lon: 100.6113273667 Alt: -27.52</h3>"
            "<h3>Target Depth ([m]): 0.36</h3><h3>Status: 4</h3><img src='AutoScreenShots/57.1.jpg' width='500'/>") in text
    assert "<styleUrl>#APWA:2</styleUrl>" in text and kml.ZONES_FOLDER not in text
    (p,) = kml.parse(text.encode())
    assert p.name == "SITE_2026-10-22_0057" and p.track == _profile().track
    (m,) = p.marks
    assert (m.name, m.standard, m.index, m.depth_m, m.status, m.type_name, m.alt) == \
        ("Mark: 57.1", "APWA", 2, 0.36, "4", "Red", -27.52)
    assert kml.mark_colour(m) == "#EE3023"  # the colour written in the .mrk file


def test_kmz_reads_like_kml_and_our_zones_are_not_reference_marks():
    p = _profile()
    p.zones = [kml.Mark(name="GPR: 57.1", lat=13.56026, lon=100.61132, index=1, depth_m=0.4)]
    data = kml.build_kmz("x.kml", kml.build_kml([p]), {"AutoScreenShots/57.1.jpg": b"jpg"}, b"png")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert set(z.namelist()) == {"x.kml", "AutoScreenShots/57.1.jpg", "logo.png"}
    (q,) = kml.parse(data)
    assert [m.name for m in q.marks] == ["Mark: 57.1"]
    with pytest.raises(ValueError):
        kml.parse(b"not xml")


def test_marker_depth_is_cut_to_the_centimetre():
    # CrossOver 800 MHz channel: ZERO LEVEL 68, 10240 MHz sampling, 0.1 m/ns, antenna separation 0.09 m
    rg = Radargram(np.zeros((1100, 3), np.float32), dt_ns=1000 / 10240, velocity_m_per_ns=0.1,
                   metadata={"zero_level_sample": 68, "velocity_m_per_ns": 0.1, "antenna_separation_m": 0.09})
    # ViewPoint printed 0.36, 0.57, 0.63, 0.68 for these samples; rounding would give 0.64 and 0.69
    assert [kml.marker_depth(rg, s) for s in (134, 176, 190, 200)] == [0.36, 0.57, 0.63, 0.68]


def test_mrk_and_cor_keep_elevation_and_quality(tmp_path):
    mrk = tmp_path / "L_0057_0.mrk"
    mrk.write_text("HEADER VERSION:300\n111\t134\tAPWA\t#EE3023\t2\t\t13.5602683332\tN\t100.6113273667\tE\t-27.52\tM\t4\tN\t"
                   "26da9867-9a2a-3850-843a-4d3d1975d678\n", encoding="latin-1")
    (m,) = read_mrk(mrk)
    assert (m["trace"], m["sample"], m["status"], m["elevation"], m["quality"]) == (111, 134, "2", -27.52, "4")
    cor = tmp_path / "L_0057.cor"
    cor.write_text("16\t2026-10-22\t14:44:43:000\t13.5602538090\tN\t100.6113348308\tE\t-27.53\tM\t4\n", encoding="latin-1")
    (g,) = read_cor(cor)
    assert (g["lat"], g["elevation"], g["quality"]) == (13.560253809, -27.53, "4")
    assert kml.profile_name("L_0057_0.iprh") == "L_0057" and kml.profile_number("L_0057") == "57"


def test_app_export_and_answer_key_import(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient

    from gpr_analyzer.app.server import create_app

    c = TestClient(create_app(tmp_path / "jobs", background=False))
    j = c.post("/api/demo").json()
    jid = j["job_id"]
    text = c.get(f"/api/jobs/{jid}/export/survey.kml").content
    profiles = kml.parse(text)
    assert [p.name for p in profiles] == ["DEMO_LINE01", "DEMO_LINE02"] and all(len(p.track) > 2 for p in profiles)
    assert kml.ZONES_FOLDER.encode() in text and b"#APWA:1" in text  # our zones, pink
    kmz = c.get(f"/api/jobs/{jid}/export/survey.kmz").content
    with zipfile.ZipFile(io.BytesIO(kmz)) as z:
        names = z.namelist()
        assert "logo.png" in names and any(n.endswith(".kml") for n in names)
        assert sum(n.startswith("AutoScreenShots/GPR_") for n in names) == text.count(b"<name>GPR: ") > 0
    # answer key: one marker on line 1 right above our first zone there
    l1 = next(ln for ln in j["lines"] if ln["source_file"] == "DEMO_LINE01.iprh")
    z = min((z for z in l1["zones"] if z["suspected_type"] == "pipe_point"), key=lambda z: z["trace_start"])
    m = c.get(f"/api/jobs/{jid}/lines/{l1['line_id']}/map.json").json()
    centre = (z["trace_start"] + z["trace_end"]) // 2
    lat, lon = m["points"][next(k for k, (a, b) in enumerate(m["slice"]["trace_groups"]) if a <= centre <= b)]
    key = kml.build_kml([kml.Profile(name="DEMO_LINE01", track=[], marks=[
        kml.Mark(name="Mark: 1.1", lat=lat, lon=lon, index=2, depth_m=z["depth_top_m"], type_name="Red")])])
    r = c.post(f"/api/jobs/{jid}/ref/kml", files={"file": ("key.kml", key.encode(), "application/xml")}).json()
    assert r["import_result"]["added"] == 1 and r["import_result"]["profiles_not_in_job"] == []
    rows = r["comparison"]["lines"][l1["line_id"]]["rows"]
    assert any(row["kind"] == "pair" and row["zone_id"] == z["zone_id"] and row["label"] == "Mark: 1.1" for row in rows)
    marks = c.get(f"/api/jobs/{jid}/marks.json").json()
    assert marks["reference"][0]["colour"] == "#EE3023" and marks["field"] == []
    # importing again replaces, not duplicates
    r = c.post(f"/api/jobs/{jid}/ref/kml", files={"file": ("key.kml", key.encode(), "application/xml")}).json()
    assert len(r["comparison"]["lines"][l1["line_id"]]["picks"]) == 1


REAL = Path(os.environ.get("GPR_REAL_DATA", ""))


@pytest.mark.skipif(not os.environ.get("GPR_REAL_DATA") or not (REAL / "Export").is_dir(),
                    reason="set GPR_REAL_DATA to a ViewPoint survey folder with its Export/*.kml")
def test_real_viewpoint_export_is_reproduced_byte_for_byte():
    src = next((REAL / "Export").glob("*.kml"))
    theirs = src.read_bytes().decode("utf-8")
    profiles = []
    for p in kml.parse(theirs.encode()):  # their folder order
        (rg,) = read_impulseradar(REAL / f"{p.name}_0.iprh")
        ours = kml.field_profile(rg, p.name)
        ours.marks = ours.marks[:len(p.marks)]  # markers added in the field after the export are not in it
        profiles.append(ours)
    assert kml.build_kml(profiles) == theirs
