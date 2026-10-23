"""Local review app: upload → review → labels → exports, on synthetic data."""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
for _m in ("gpr_analyzer.processing.auto", "gpr_analyzer.detect.pipeline", "gpr_analyzer.report.schema", "gpr_analyzer.io.impulseradar"):
    pytest.importorskip(_m)  # stages from PRs #1–#4
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app.demo import write_demo_files  # noqa: E402
from gpr_analyzer.app.files import safe_name, sort_files  # noqa: E402
from gpr_analyzer.app.server import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path / "jobs"))


def test_sort_files_pairs_and_companions(tmp_path):
    names = ["A.iprh", "A.iprb", "A.txt", "B.HD", "B.DT1", "orphan.txt", "mystery.dat"]
    paths = [tmp_path / n for n in names]
    s = sort_files(paths)
    prim = {g.primary.name for g in s.groups}
    assert prim == {"A.iprh", "B.HD"}
    a = next(g for g in s.groups if g.stem == "A")
    assert [p.name for p in a.companions] == ["A.txt"]
    assert {p.name for p in s.unassigned} == {"orphan.txt", "mystery.dat"}
    assert safe_name("..\\..\\evil/../x y.iprb") == "x y.iprb"


def test_upload_review_labels_export(client, tmp_path):
    files = write_demo_files(tmp_path / "in") + [tmp_path / "in" / "mystery.dat"]
    files[-1].write_bytes(b"\x00" * 100)
    up = [("files", (p.name, p.read_bytes())) for p in files]
    j = client.post("/api/jobs", files=up).json()
    jid = j["job_id"]
    assert [ln["source_file"] for ln in j["lines"]] == ["DEMO_LINE01.iprh", "DEMO_LINE02.iprh"]
    assert {c["name"] for c in j["lines"][0]["companions"]} == {"DEMO_LINE01.cor", "DEMO_LINE01.txt"}
    assert any(p["file"] == "mystery.dat" and p["error"] == "unknown_format" for p in j["problems"])
    assert j["draft"] is True
    ln = j["lines"][0]
    assert ln["zones"], "demo line should give zones"
    for v in ("grey", "high_gain", "envelope"):
        r = client.get(f"/api/jobs/{jid}/lines/L1/{v}.png")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"

    z0, z1 = ln["zones"][0]["zone_id"], ln["zones"][1]["zone_id"]
    j = client.post(f"/api/jobs/{jid}/lines/L1/zones/{z0}/review", json={"action": "accept"}).json()
    assert j["lines"][0]["zones"][0]["review"] == "accepted"
    j = client.post(f"/api/jobs/{jid}/lines/L1/zones/{z1}/review",
                    json={"action": "edit", "changes": {"suspected_type": "void_like", "confidence": "low",
                                                        "trace_start": 120, "trace_end": 100}, "note": "check"}).json()
    z = j["lines"][0]["zones"][1]
    assert (z["review"], z["suspected_type"], z["trace_start"], z["trace_end"], z["note"]) == \
        ("edited", "void_like", 100, 120, "check")
    r = client.post(f"/api/jobs/{jid}/lines/L1/zones/{z1}/review", json={"action": "edit", "changes": {"confidence": "confirmed"}})
    assert r.status_code == 400  # no uncalibrated "confirmed" without ground truth
    j = client.post(f"/api/jobs/{jid}/lines/L1/zones", json={"trace_start": 10, "trace_end": 30, "time_top_ns": 20,
                                                              "time_bottom_ns": 30}).json()
    assert any(z["zone_id"] == "M1" and z["manual"] for z in j["lines"][0]["zones"])
    assert client.post(f"/api/jobs/{jid}/lines/L1/zones/{z0}/review", json={"action": "reject"}).status_code == 200

    labels = [json.loads(s) for s in client.get(f"/api/jobs/{jid}/labels.jsonl").text.splitlines()]
    assert [r["action"] for r in labels] == ["accept", "edit", "add", "reject"]
    assert labels[1]["auto"]["zone_id"] == z1 and labels[1]["after"]["suspected_type"] == "void_like"

    for name, magic in (("report.pdf", b"%PDF"), ("report.docx", b"PK"), ("zones.csv", None), ("survey.json", b"{")):
        r = client.get(f"/api/jobs/{jid}/export/{name}")
        assert r.status_code == 200, name
        if magic:
            assert r.content.startswith(magic), name
    for fmt in ("sgy", "csv"):
        assert client.get(f"/api/jobs/{jid}/lines/L2/export/{fmt}").status_code == 200


def test_reprocess_with_user_velocity(client):
    j = client.post("/api/demo").json()
    jid = j["job_id"]
    j = client.post(f"/api/jobs/{jid}/lines/L2/reprocess", json={"options": {"velocity_m_per_ns": 0.09, "gain_max_db": 40}}).json()
    ln = j["lines"][1]
    assert ln["velocity_m_per_ns"] == pytest.approx(0.09) and ln["velocity_source_code"] == "user"
    assert ln["options"] == {"velocity_m_per_ns": 0.09, "gain_max_db": 40.0}
    assert all(z["review"] == "auto" for z in ln["zones"])
    bad = client.post(f"/api/jobs/{jid}/lines/L2/reprocess", json={"options": {"background": "magic"}})
    assert bad.status_code == 400
