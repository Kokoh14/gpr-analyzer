"""Comparison with reference software (ViewPoint / GeoLitix) and reopening saved jobs."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app import compare as cmp  # noqa: E402
from gpr_analyzer.app.server import create_app  # noqa: E402


def test_parse_csv_headers_units_and_thai_encoding():
    data = "Name;Type;Distance (m);Depth (cm)\nP1;void;12,5;60\nP2;pipe;3;40\n".encode("cp874")
    picks, warn = cmp.parse_picks_csv(data, "geolitix")
    assert warn == [] and len(picks) == 2
    assert picks[0]["x_start"] == 12.5 and picks[0]["top"] == pytest.approx(0.6) and picks[0]["label"] == "P1"
    th = "ชื่อ,ระยะเริ่ม,ระยะสิ้นสุด,ความลึกบน,ความลึกล่าง\nก,1,2,0.5,0.8\n".encode("utf-8-sig")
    (p,), _ = cmp.parse_picks_csv(th, "viewpoint")
    assert (p["x_start"], p["x_end"], p["top"], p["bottom"], p["label"]) == (1, 2, 0.5, 0.8, "ก")
    t = b"trace,time_ns\n100,20\n"
    (p,), _ = cmp.parse_picks_csv(t, "other")
    assert (p["x_unit"], p["y_unit"]) == ("trace", "ns")
    with pytest.raises(ValueError, match="Columns found"):
        cmp.parse_picks_csv(b"foo,bar\n1,2\n", "other")
    picks, _ = cmp.parse_picks_csv(cmp.TEMPLATE_CSV.encode(), "viewpoint")
    assert len(picks) == 2


def test_matching_counts_and_overrides():
    g = cmp.LineGeom(n_traces=500, dx_m=0.05, time_window_ns=100, velocity_m_per_ns=0.1)
    zones = [
        {"zone_id": "A", "trace_start": 100, "trace_end": 140, "time_top_ns": 20, "time_bottom_ns": 30,
         "depth_top_m": 1.0, "suspected_type": "void_like", "review": "auto"},
        {"zone_id": "B", "trace_start": 300, "trace_end": 320, "time_top_ns": 10, "time_bottom_ns": 14,
         "depth_top_m": 0.5, "suspected_type": "pipe_point", "review": "auto"},
        {"zone_id": "X", "trace_start": 400, "trace_end": 420, "time_top_ns": 10, "time_bottom_ns": 14,
         "depth_top_m": 0.5, "suspected_type": "void_like", "review": "rejected"},
    ]
    ls = {"meta": {}, "verdicts": {}, "images": [], "picks": [
        cmp.clean_pick({"source": "viewpoint", "x_start": 6.0, "x_end": 6.5, "top": 1.1, "bottom": 1.4}, "R1"),
        cmp.clean_pick({"source": "viewpoint", "x_start": 20.5, "top": 0.4}, "R2"),  # 410 traces: only rejected X there
    ]}
    out = cmp.compare_line(ls, zones, g, cmp.DEFAULT_TOLERANCE)
    rows = {r["ref_id"] or r["zone_id"]: r for r in out["rows"]}
    assert rows["R1"]["zone_id"] == "A" and rows["R1"]["ddepth_top_m"] == pytest.approx(-0.1)
    assert rows["R2"]["kind"] == "ref_only" and rows["B"]["kind"] == "ours_only"
    s = out["summary"]
    assert (s["n_ref"], s["match"], s["missed"], s["extra"], s["recall"]) == (2, 1, 1, 1, 0.5)

    ls["picks"][1]["match_zone"] = "B"  # forced pairing
    ls["verdicts"]["ref:R2"] = {"verdict": "partial", "note": ""}
    s = cmp.compare_line(ls, zones, g, cmp.DEFAULT_TOLERANCE)["summary"]
    assert (s["match"], s["partial"], s["extra"], s["recall"], s["precision"]) == (1, 1, 0, 1.0, 1.0)

    ls["meta"]["viewpoint"] = cmp.clean_meta({"n_traces": "500", "time_window_ns": "120", "velocity_m_per_ns": "0.1"})
    mc = {m["field"]: m["ok"] for m in cmp.compare_line(ls, zones, g, cmp.DEFAULT_TOLERANCE)["meta_check"]}
    assert mc == {"n_traces": True, "time_window_ns": False, "velocity_m_per_ns": True}


def test_api_compare_flow_and_reopen(tmp_path):
    root = tmp_path / "jobs"
    c = TestClient(create_app(root, background=False))
    j = c.post("/api/demo").json()
    jid, ln = j["job_id"], j["lines"][0]
    z = ln["zones"][0]
    x = (z["trace_start"] + z["trace_end"]) / 2 * ln["dx_m"]
    csv = f"label,distance,depth\nG1,{x:.2f},{z['depth_top_m']:.2f}\n".encode()
    r = c.post(f"/api/jobs/{jid}/lines/L1/ref/import", files={"file": ("g.csv", csv)}, data={"source": "geolitix"})
    assert r.status_code == 200 and r.json()["import_result"]["added"] == 1
    j = c.post(f"/api/jobs/{jid}/lines/L1/ref/picks",
               json={"source": "viewpoint", "x_unit": "trace", "y_unit": "ns", "x_start": 5, "x_end": 9,
                     "top": 1, "bottom": 2}).json()
    rows = j["comparison"]["lines"]["L1"]["rows"]
    assert rows[0]["zone_id"] == z["zone_id"] and rows[0]["auto"] == "match"
    assert c.post(f"/api/jobs/{jid}/lines/L1/ref/verdict", json={"key": "ref:R2", "verdict": "missed"}).status_code == 200
    assert c.post(f"/api/jobs/{jid}/lines/L1/ref/verdict", json={"key": "ref:R2", "verdict": "bogus"}).status_code == 400
    r = c.post(f"/api/jobs/{jid}/lines/L1/ref/images", files=[("files", ("shot.png", b"\x89PNG\r\n"))],
               data={"source": "viewpoint"})
    img = r.json()["comparison"]["lines"]["L1"]["images"][0]
    assert c.get(f"/api/jobs/{jid}/lines/L1/ref/images/{img['file']}").content == b"\x89PNG\r\n"
    assert c.get(f"/api/jobs/{jid}/lines/L1/ref/images/..%2Fstate.json").status_code == 404
    bad = c.post(f"/api/jobs/{jid}/lines/L1/ref/images", files=[("files", ("x.exe", b"MZ"))], data={"source": "viewpoint"})
    assert bad.status_code == 400
    c.post(f"/api/jobs/{jid}/compare/settings", json={"features": [{"feature": "Migration", "source": "viewpoint",
                                                                    "rating": "worse", "note": "slow"}]})
    sheet = c.get(f"/api/jobs/{jid}/export/comparison.csv").text
    assert sheet.startswith("﻿job_id,") and "G1" in sheet and "missed" in sheet
    assert "worse" in c.get(f"/api/jobs/{jid}/export/features.csv").text
    c.post(f"/api/jobs/{jid}/lines/L1/zones/{z['zone_id']}/review", json={"action": "reject"})

    # a new app on the same folder (= the program was closed and opened again)
    c2 = TestClient(create_app(root, background=False))
    listed = c2.get("/api/jobs").json()
    assert listed[0]["job_id"] == jid and listed[0]["compare"]["n_ref"] == 2
    j2 = c2.get(f"/api/jobs/{jid}").json()
    assert j2["lines"][0]["zones"][0]["review"] == "rejected"
    assert len(j2["comparison"]["lines"]["L1"]["picks"]) == 2
    all_lines = c2.get("/api/compare/export/comparison_lines_all.csv").text
    assert jid in all_lines
    assert c2.get("/api/jobs/..%2F..%2Fetc").status_code == 404
