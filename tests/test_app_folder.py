"""Data folder on this computer: completeness check, distance range, background run, log, checkpoints, resume."""

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app import folders  # noqa: E402
from gpr_analyzer.app.demo import write_demo_files  # noqa: E402
from gpr_analyzer.app.jobs import Job, LineState, crop_to_range  # noqa: E402
from gpr_analyzer.app.server import create_app  # noqa: E402
from gpr_analyzer.io import read  # noqa: E402


@pytest.fixture()
def survey(tmp_path):
    d = tmp_path / "survey"
    write_demo_files(d)
    (d / "BROKEN.iprh").write_text("SAMPLES: 0\n", encoding="latin-1")
    (d / "BROKEN.iprb").write_bytes(b"\0" * 64)
    (d / "NOGPS.iprh").write_bytes((d / "DEMO_LINE02.iprh").read_bytes())
    (d / "NOGPS.iprb").write_bytes((d / "DEMO_LINE02.iprb").read_bytes()[:-100])  # last trace cut short
    (d / "sub").mkdir()
    return d


def test_scan_folder_reports_each_line(survey):
    r = folders.scan_folder(survey)
    by = {ln["name"]: ln for ln in r["lines"]}
    assert set(by) == {"BROKEN", "DEMO_LINE01", "DEMO_LINE02", "NOGPS"}
    ok = by["DEMO_LINE01"]
    assert ok["n_traces"] == 555 and ok["length_m"] == pytest.approx(27.75) and ok["gps_fixes"] == 56
    assert len(ok["track"]) == 200 and ok["gps_coverage"] > 0.95
    assert ok["positioning"]["traces_per_m"] == 20
    assert ok["positioning"]["gps_points_per_m"] > 1
    assert ok["positioning"]["start_time"] and not ok["positioning"]["sharp_turns"]
    assert ok["status"] in ("ok", "warn") and not [i for i in ok["issues"] if i["level"] == "warn"]
    assert by["BROKEN"]["status"] == "error"
    codes = {i["code"] for i in by["NOGPS"]["issues"]}
    assert {"no_gps", "partial_trace"} <= codes and by["NOGPS"]["status"] == "warn"
    assert r["summary"]["lines"] == 4 and r["summary"]["error"] == 1
    listing = folders.list_dir(str(survey))
    assert listing["dirs"][0]["name"] == "sub" and listing["gpr_files"] == 8


def test_crop_to_range_shifts_gps_and_keeps_ends():
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp())
    write_demo_files(d)
    rg = read(d / "DEMO_LINE01.iprh")[0]
    out, t0 = crop_to_range(rg, 10.0, 15.0)
    assert t0 == 200 and out.n_traces == 101 and out.metadata["crop"]["n_traces_file"] == 555
    trs = [g["trace"] for g in out.metadata["gps"]]
    assert trs[0] <= 0 and trs[-1] >= 100  # a fix outside each end is kept for interpolation
    with pytest.raises(ValueError):
        crop_to_range(rg, 27.7, None)


def wait_done(c, jid, timeout=120):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        p = c.get(f"/api/jobs/{jid}/progress").json()
        if p["state"] == "done":
            return p
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_folder_job_background_range_log_and_reopen(survey, tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    c = TestClient(create_app(root))
    scan = c.post("/api/scan", json={"path": str(survey)}).json()
    keep = [ln["primary"] for ln in scan["lines"] if ln["name"].startswith("DEMO")]
    first = next(p for p in keep if "LINE01" in p)
    j = c.post("/api/jobs/from-folder", json={"path": str(survey), "include": keep, "ranges": {first: [5, 20]},
                                              "info": {"project": "folder test"}}).json()
    jid = j["job_id"]
    assert j["source_folder"] == str(survey) and j["progress"]["total"] == 2
    p = wait_done(c, jid)
    assert p["done"] == 2 and p["fraction"] == 1.0
    j = c.get(f"/api/jobs/{jid}").json()
    l1 = j["lines"][0]
    assert l1["trace_offset"] == 100 and l1["n_traces"] == 301 and l1["crop"]["start_m"] == 5.0
    assert all(z["distance_start_m"] >= 5.0 - 1e-6 for z in l1["zones"] if z["distance_start_m"] is not None)
    log = c.get(f"/api/jobs/{jid}/log.txt").text
    assert "เริ่มประมวลผล 2" in log and "L1 DEMO_LINE01.iprh ช่วง 5.0–20.1 m" in log and "เสร็จ: 2 เส้น" in log
    assert not (root / jid / "uploads" / "DEMO_LINE01.iprb").exists()  # read in place, not copied
    z = l1["zones"][0]["zone_id"]
    c.post(f"/api/jobs/{jid}/lines/L1/zones/{z}/review", json={"action": "accept"})

    # reopened by a new app: lines come from checkpoints, nothing is reprocessed
    monkeypatch.setattr(LineState, "process", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reprocessed")))
    c2 = TestClient(create_app(root))
    j2 = c2.get(f"/api/jobs/{jid}").json()
    assert [ln["line_id"] for ln in j2["lines"]] == ["L1", "L2"] and j2["progress"]["state"] == "done"
    assert j2["lines"][0]["zones"][0]["review"] == "accepted"
    assert c2.get("/api/jobs").json()[0]["state"] == "done"


def test_interrupted_job_resumes(survey, tmp_path):
    root = tmp_path / "jobs"
    c = TestClient(create_app(root, background=False))
    jid = c.post("/api/jobs/from-folder", json={"path": str(survey)}).json()["job_id"]
    # simulate a power cut after the first line: forget the later ones and their checkpoints
    job = Job.restore(root / jid)
    done = [e for e in job.plan if e["state"] == "done"]
    for e in done[1:]:
        for lid in e["line_ids"]:
            job.checkpoint_path(lid).unlink()
    c2 = TestClient(create_app(root, background=False))
    j = c2.get(f"/api/jobs/{jid}").json()
    assert j["progress"]["state"] == "interrupted" and len(j["lines"]) == 1
    assert c2.get("/api/jobs").json()[0]["state"] == "interrupted"
    j = c2.post(f"/api/jobs/{jid}/resume").json()
    assert j["progress"]["state"] == "done" and len(j["lines"]) == len(done)
    assert "ทำต่อ" in c2.get(f"/api/jobs/{jid}/log.txt").text
