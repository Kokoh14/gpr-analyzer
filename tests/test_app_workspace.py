"""Review workspace: panels chosen before processing; optional views left out are made when first shown."""

import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app.demo import write_demo_files  # noqa: E402
from gpr_analyzer.app.jobs import OPTIONAL_VIEWS, process_line  # noqa: E402
from gpr_analyzer.app.server import create_app  # noqa: E402
from gpr_analyzer.io import read as read_file  # noqa: E402
from gpr_analyzer.processing import ProcessingOptions  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path / "jobs", background=False))


def test_skipped_views_do_not_change_analysis_or_zones(tmp_path):
    raw = read_file(write_demo_files(tmp_path)[0])[0]
    full, zf, _ = process_line(raw, ProcessingOptions(), "L1")
    slim, zs, _ = process_line(raw, ProcessingOptions(), "L1", skip=OPTIONAL_VIEWS)
    assert all(getattr(slim, v) is None for v in OPTIONAL_VIEWS)
    assert np.array_equal(full.analysis.data, slim.analysis.data)
    assert zf == zs
    # a view made later on its own is the same picture as one made with everything
    one, _, _ = process_line(raw, ProcessingOptions(), "L1", skip=("layers", "autodisplay"))
    assert np.array_equal(one.viewpoint.data, full.viewpoint.data)


def test_workspace_skips_views_and_makes_them_on_open(client, tmp_path):
    files = write_demo_files(tmp_path / "in")
    ws = {"layout": "risk", "panels": {"viewpoint": "open", "layers": "off", "autodisplay": "now", "map": "now"}}
    up = [("files", (p.name, p.read_bytes())) for p in files]
    j = client.post("/api/jobs", files=up, data={"workspace": json.dumps(ws)}).json()
    jid, ln = j["job_id"], j["lines"][0]
    assert j["workspace"]["layout"] == "risk" and j["workspace"]["panels"]["layers"] == "off"
    assert "autodisplay" in ln["views"] and {"viewpoint", "layers"} <= set(ln["lazy_views"])
    assert not any(s["step"] == "viewpoint_display" for s in ln["steps"])

    r = client.get(f"/api/jobs/{jid}/lines/L1/viewpoint.png")  # opened later: made now
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    ln = client.get(f"/api/jobs/{jid}").json()["lines"][0]
    assert "viewpoint" in ln["views"] and "viewpoint" not in ln["lazy_views"]
    assert sum(s["step"] == "viewpoint_display" for s in ln["steps"]) == 1

    # changing the workspace later is saved with the job
    w = client.put(f"/api/jobs/{jid}/workspace", json={"layout": "detail", "panels": {"layers": "now"}, "open": ["map"]}).json()
    assert w == {"layout": "detail", "panels": {"layers": "now"}, "open": ["map"]}
    state = json.loads((tmp_path / "jobs" / jid / "state.json").read_text(encoding="utf-8"))
    assert state["workspace"]["layout"] == "detail"
    assert client.put(f"/api/jobs/{jid}/workspace", json={"panels": {"map": "maybe"}}).status_code == 400


def test_no_workspace_keeps_every_view(client, tmp_path):
    up = [("files", (p.name, p.read_bytes())) for p in write_demo_files(tmp_path / "in")]
    ln = client.post("/api/jobs", files=up).json()["lines"][0]
    assert set(OPTIONAL_VIEWS) <= set(ln["views"]) and ln["lazy_views"] == []
