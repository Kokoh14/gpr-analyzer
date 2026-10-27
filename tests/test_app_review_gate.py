"""Before the report: excluded sections, line approval, reprocessing that keeps reviews, summary."""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app.server import create_app  # noqa: E402


@pytest.fixture()
def demo(tmp_path):
    c = TestClient(create_app(tmp_path / "jobs", background=False))
    j = c.post("/api/demo").json()
    return c, j


def test_excluded_section_hides_zones_from_report(demo):
    c, j = demo
    jid, ln = j["job_id"], j["lines"][0]
    z = next(z for z in ln["zones"] if z["trace_end"] - z["trace_start"] < 200)
    j = c.post(f"/api/jobs/{jid}/lines/L1/sections", json={"trace_start": z["trace_end"] + 5, "trace_end": z["trace_start"] - 5,
                                                           "reason": "รถจอดทับ"}).json()
    sec = j["lines"][0]["sections"][0]
    assert sec["section_id"] == "S1" and sec["trace_start"] < sec["trace_end"] and sec["distance_start_m"] is not None
    zz = next(x for x in j["lines"][0]["zones"] if x["zone_id"] == z["zone_id"])
    assert zz["excluded"] == "S1"
    survey = json.loads(c.get(f"/api/jobs/{jid}/export/survey.json").text)
    l1 = survey["lines"][0]
    assert z["zone_id"] not in {x["zone_id"] for x in l1["zones"]}
    assert any(a["reason"] == "excluded_by_reviewer" for a in l1["artifact_regions"])
    assert c.get(f"/api/jobs/{jid}/export/report.pdf").status_code == 200
    j = c.delete(f"/api/jobs/{jid}/lines/L1/sections/S1").json()
    assert j["lines"][0]["sections"] == []
    assert c.post(f"/api/jobs/{jid}/lines/L1/sections", json={"trace_start": 3, "trace_end": 3}).status_code == 400
    acts = [json.loads(s)["action"] for s in c.get(f"/api/jobs/{jid}/labels.jsonl").text.splitlines()]
    assert acts == ["exclude_section", "restore_section"]


def test_reprocess_keeps_reviews_and_drawn_zones(demo):
    c, j = demo
    jid, ln = j["job_id"], j["lines"][0]
    z0, z1 = ln["zones"][0]["zone_id"], ln["zones"][1]["zone_id"]
    c.post(f"/api/jobs/{jid}/lines/L1/zones/{z0}/review", json={"action": "reject", "note": "ringing"})
    c.post(f"/api/jobs/{jid}/lines/L1/zones/{z1}/review", json={"action": "accept"})
    c.post(f"/api/jobs/{jid}/lines/L1/zones", json={"trace_start": 10, "trace_end": 30, "time_top_ns": 20, "time_bottom_ns": 30})
    c.post(f"/api/jobs/{jid}/lines/L1/status", json={"status": "redo", "note": "ลอง bandpass ใหม่"})
    # same options again: the same zones come back, so every decision is carried over
    j = c.post(f"/api/jobs/{jid}/lines/L1/reprocess", json={"options": {"gain_max_db": 60}}).json()
    zs = {z["zone_id"]: z for z in j["lines"][0]["zones"]}
    assert zs[z0]["review"] == "rejected" and zs[z0]["note"] == "ringing" and zs[z1]["review"] == "accepted"
    assert zs["M1"]["manual"]
    assert j["lines"][0]["status"] == "pending"  # "redo" is cleared by reprocessing
    last = json.loads(c.get(f"/api/jobs/{jid}/labels.jsonl").text.splitlines()[-1])
    assert last["action"] == "reprocess" and len(last["after"]["reviews_carried"]) == 3
    assert c.post(f"/api/jobs/{jid}/lines/L1/status", json={"status": "maybe"}).status_code == 400


def test_summary_lists_findings_and_warnings(demo):
    c, j = demo
    jid = j["job_id"]
    s = c.get(f"/api/jobs/{jid}/summary").json()
    assert s["totals"]["lines"] == 2 and not s["ready"]
    codes = {(w["line_id"], w["code"]) for w in s["warnings"]}
    assert ("L1", "unreviewed") in codes and ("L1", "not_approved") in codes and (None, "missing_info") in codes
    assert all(f["action"] in ("E", "1", "2") for f in s["findings"])
    # demo lines have GPS, so every finding carries a coordinate (the demo may have none at E/1/2: its
    # cavity sits between full-depth stripes; an earlier finding here was a false void on a stripe)
    assert all(ln["has_gps"] for ln in s["lines"])
    assert all(f["coord"] is not None for f in s["findings"])
    for ln in j["lines"]:
        for z in ln["zones"]:
            c.post(f"/api/jobs/{jid}/lines/{ln['line_id']}/zones/{z['zone_id']}/review", json={"action": "accept"})
        c.post(f"/api/jobs/{jid}/lines/{ln['line_id']}/status", json={"status": "approved"})
    s = c.get(f"/api/jobs/{jid}/summary").json()
    assert {w["code"] for w in s["warnings"]} <= {"default_velocity", "missing_info"}
    assert s["totals"]["approved"] == 2


def test_cross_line_reason_added_once(demo):
    c, j = demo
    jid = j["job_id"]

    def counts(j):
        return [sum(e["code"].startswith("cross_line") for e in z["evidence"])
                for ln in j["lines"] for z in ln["zones"]
                if z["suspected_type"] in ("void_like", "scattered", "unknown", "pipe_point")]

    first = counts(j)
    assert first and set(first) == {1}  # the two demo passes are ~9 m apart: not assessed, but said so
    j = c.post(f"/api/jobs/{jid}/lines/L2/reprocess", json={"options": {}}).json()
    assert set(counts(j)) == {1}


def test_approve_all_lines_whose_zones_are_reviewed(demo):
    c, j = demo
    jid = j["job_id"]
    first, second = j["lines"][0], j["lines"][1]
    assert first["zones"] and second["zones"]
    for z in first["zones"]:
        c.post(f"/api/jobs/{jid}/lines/{first['line_id']}/zones/{z['zone_id']}/review", json={"action": "accept"})
    r = c.post(f"/api/jobs/{jid}/approve-reviewed").json()
    # only the fully reviewed line is approved; the other still has unreviewed zones
    assert r["approved"] == [first["line_id"]]
    status = {ln["line_id"]: ln["status"] for ln in r["job"]["lines"]}
    assert status == {first["line_id"]: "approved", second["line_id"]: "pending"}
    # a line marked for reprocessing is never approved by the bulk button
    for z in second["zones"]:
        c.post(f"/api/jobs/{jid}/lines/{second['line_id']}/zones/{z['zone_id']}/review", json={"action": "reject"})
    c.post(f"/api/jobs/{jid}/lines/{second['line_id']}/status", json={"status": "redo"})
    assert c.post(f"/api/jobs/{jid}/approve-reviewed").json()["approved"] == []
    acts = [json.loads(s)["action"] for s in c.get(f"/api/jobs/{jid}/labels.jsonl").text.splitlines()]
    assert acts.count("line_status") == 2  # the bulk approval is recorded like a click, plus the "redo"
