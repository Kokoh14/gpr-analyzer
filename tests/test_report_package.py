"""One-click report package: confirmed-only content, draft gate, traceable numbers, KMZ, snapshots."""

import csv
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app.server import create_app  # noqa: E402
from gpr_analyzer.report.compose import Signoff, compose  # noqa: E402
from gpr_analyzer.report.figures import render_overview_map  # noqa: E402
from gpr_analyzer.report.rules import find_wording_violations  # noqa: E402
from gpr_analyzer.report.schema import SurveyResult  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x01\x08\x02\x00\x00\x00\x7b\x40\xe8\xdd"
       b"\x00\x00\x00\x0fIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\xa5\xe2\x1f\x8b\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture()
def demo(tmp_path):
    app = create_app(tmp_path / "jobs", background=False)
    c = TestClient(app)
    j = c.post("/api/demo").json()
    return c, j, app.state.store.get(j["job_id"])


def confirm_all(c, j, reject_first=True):
    """Reject the first zone of each line (with a note), accept the rest, approve every line."""
    jid = j["job_id"]
    c.post(f"/api/jobs/{jid}/info", json={"reviewer": "ผู้ตรวจ ทดสอบ", "project": "Demo"})
    for ln in j["lines"]:
        for i, z in enumerate(ln["zones"]):
            body = {"action": "reject", "note": "ringing"} if reject_first and i == 0 else {"action": "accept"}
            c.post(f"/api/jobs/{jid}/lines/{ln['line_id']}/zones/{z['zone_id']}/review", json=body)
        c.post(f"/api/jobs/{jid}/lines/{ln['line_id']}/status", json={"status": "approved"})


def package(c, jid):
    r = c.get(f"/api/jobs/{jid}/report-package")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    return r, zipfile.ZipFile(io.BytesIO(r.content))


def test_unconfirmed_job_gives_a_draft_package(demo):
    c, j, job = demo
    assert j["draft"] and len(j["draft_reasons"]) == 3  # zones unreviewed, lines not approved, no reviewer
    r, z = package(c, j["job_id"])
    assert "DRAFT" in r.headers["content-disposition"]
    assert {"DRAFT_report.pdf", "DRAFT_report.docx", "zones.csv", "zones.kmz", "survey.json",
            "manifest.json"} <= set(z.namelist())
    m = json.loads(z.read("manifest.json"))
    assert m["status"] == "draft"
    assert [d["code"] for d in m["draft_reasons"]] == ["unreviewed", "not_approved", "no_reviewer"]
    assert m["zones"]["unreviewed"] == m["zones"]["reported"] > 0
    rep = compose(job.survey(), lang="th", figures=False, signoff=job.signoff())
    assert rep.draft and any("ยังไม่ได้ตรวจทาน" in t for t in rep.draft_text)
    kml = zipfile.ZipFile(io.BytesIO(z.read("zones.kmz"))).read("doc.kml").decode()
    assert "NOT YET REVIEWED" in kml


def test_reviewed_zones_alone_do_not_make_a_final_report(demo):
    """Every zone accepted but the lines not approved: still a draft (a missed zone is not ruled out)."""
    c, j, job = demo
    jid = j["job_id"]
    c.post(f"/api/jobs/{jid}/info", json={"reviewer": "A"})
    for ln in j["lines"]:
        for z in ln["zones"]:
            c.post(f"/api/jobs/{jid}/lines/{ln['line_id']}/zones/{z['zone_id']}/review", json={"action": "accept"})
    assert not job.survey().unreviewed
    assert [r[0] for r in job.draft_reasons()] == ["not_approved"]
    assert compose(job.survey(), figures=False, signoff=job.signoff()).draft
    assert c.get(f"/api/jobs/{jid}/summary").json()["final"] is False


def test_confirmed_package_holds_only_confirmed_zones_with_traceable_numbers(demo):
    c, j, job = demo
    confirm_all(c, j)
    s = c.get(f"/api/jobs/{j['job_id']}/summary").json()
    assert s["final"] and s["draft_reasons"] == []
    r, z = package(c, j["job_id"])
    assert "DRAFT" not in r.headers["content-disposition"]
    assert {"report.pdf", "report.docx", "labels.jsonl"} <= set(z.namelist())
    m = json.loads(z.read("manifest.json"))
    assert m["status"] == "final" and m["reviewer"] == "ผู้ตรวจ ทดสอบ"
    for name, meta in m["files"].items():
        assert hashlib.sha256(z.read(name)).hexdigest() == meta["sha256"]

    # every input file (header, binary data, GPS) is listed with the hash of the file on disk
    names = {f["name"]: f for f in m["inputs"]}
    assert {"DEMO_LINE01.iprh", "DEMO_LINE01.iprb", "DEMO_LINE01.cor", "DEMO_LINE02.iprb"} <= set(names)
    for f in names.values():
        p = job.upload_dir / f["name"]
        assert f["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest() and f["size"] == p.stat().st_size

    survey = SurveyResult.from_json(z.read("survey.json").decode())
    rejected = {(x.line_id, x.zone_id) for x in survey.zones if x.review == "rejected"}
    kept = [x for x in survey.zones if x.review != "rejected"]
    assert len(rejected) == 2 and m["zones"] == {"reported": len(kept), "rejected": 2, "unreviewed": 0}

    # CSV rows carry the same numbers as survey.json (the CSV keeps rejected rows, marked as such)
    rows = list(csv.DictReader(io.StringIO(z.read("zones.csv").decode("utf-8-sig"))))
    by = {(x.line_id, x.zone_id): x for x in survey.zones}
    for row in rows:
        x = by[(row["line_id"], row["zone_id"])]
        assert float(row["depth_top_m"]) == pytest.approx(x.depth_top_m, abs=0.005)  # CSV keeps cm
        assert float(row["depth_bottom_m"]) == pytest.approx(x.depth_bottom_m, abs=0.005)
        assert row["review"] == x.review

    # KMZ: rejected zones left out, kept zones at their stored coordinates
    kml = zipfile.ZipFile(io.BytesIO(z.read("zones.kmz"))).read("doc.kml").decode()
    assert "NOT YET REVIEWED" not in kml
    for lid, zid in rejected:
        assert f"<name>{lid}:{zid}</name>" not in kml
    for x in kept:
        assert f"<name>{x.line_id}:{x.zone_id}</name>" in kml
        assert f"{x.coord_start[0]:.8f},{x.coord_start[1]:.8f},0" in kml

    # report text: rejected zones only in the review record, never in the zone table or the counts
    rep = compose(survey, lang="en", figures=False, signoff=job.signoff())
    assert not rep.draft
    table = next(b for b in rep.blocks if getattr(b, "header", None) and "Confidence" in b.header)
    assert {(row[1], row[0]) for row in table.rows} == {(x.line_id, x.zone_id) for x in kept}
    rej = next(b for b in rep.blocks if getattr(b, "header", None) and b.header[-1] == "Note"
               and any(r[-1] == "ringing" for r in b.rows))
    assert {(row[1], row[0]) for row in rej.rows} == rejected
    text = rep.all_text()
    assert "Confirmed: every zone reviewed" in text and "ผู้ตรวจ ทดสอบ" in text
    assert f"{len(kept)} anomaly zone(s)" in text


def test_snapshots_go_into_the_report(demo):
    c, j, job = demo
    jid = j["job_id"]
    assert c.post(f"/api/jobs/{jid}/snapshots", files={"file": ("x.png", b"not a png", "image/png")}).status_code == 400
    lst = c.post(f"/api/jobs/{jid}/snapshots", files={"file": ("x.png", PNG, "image/png")},
                 data={"caption": "มุมมอง 3D ทั้งงาน", "source": "3d"}).json()
    assert lst[0]["snapshot_id"] == "P1" and lst[0]["source"] == "3d"
    assert c.get(f"/api/jobs/{jid}/snapshots/P1.png").content == PNG
    so = job.signoff()
    assert so.snapshots[0]["png"] == PNG
    text = compose(job.survey(), lang="bi", signoff=so).all_text()
    assert "มุมมอง 3D ทั้งงาน" in text and "ช่องว่างระหว่างเส้นไม่ได้สำรวจ" in text
    assert find_wording_violations(text) == []
    assert c.delete(f"/api/jobs/{jid}/snapshots/P1").json() == []
    assert c.get(f"/api/jobs/{jid}/snapshots/P1.png").status_code == 404


def test_changed_input_file_makes_the_report_a_draft(demo):
    c, j, job = demo
    confirm_all(c, j)
    assert job.draft_reasons() == []
    p = job.upload_dir / "DEMO_LINE01.iprb"
    time.sleep(0.01)
    p.write_bytes(p.read_bytes()[:-4] + b"\0\0\0\0")
    assert [r[0] for r in job.draft_reasons()] == ["inputs_changed"]


def test_overview_map():
    assert render_overview_map({}, []) is None
    import numpy as np

    lat, lon = np.linspace(13.0, 13.0002, 50), np.linspace(100.0, 100.0003, 50)
    png = render_overview_map({"L1": (lat, lon)}, [])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_cli_report_without_signoff_keeps_old_draft_rule(demo):
    """No signoff (CLI / Python use): draft only while zones are unreviewed, as before."""
    c, j, job = demo
    s = job.survey()
    assert compose(s, figures=False).draft
    for ln in s.lines:
        ln.zones = [type(z).from_dict({**z.to_dict(), "review": "accepted"}) for z in ln.zones]
    rep = compose(s, figures=False)
    assert not rep.draft and "Review record" not in rep.all_text()


def test_single_report_export_uses_the_same_gate(demo, tmp_path):
    c, j, job = demo
    r = c.get(f"/api/jobs/{j['job_id']}/export/report.html")
    assert r.status_code == 200 and "ไม่ได้ระบุชื่อผู้ตรวจทาน" in r.text
    assert Path(job.folder / "exports" / "report.html").is_file()
