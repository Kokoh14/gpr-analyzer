"""Confirm-screen queue (app/triage.py): zones, moisture screen, audit picks, decisions saved and logged."""
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from gpr_analyzer.app.server import create_app  # noqa: E402
from gpr_analyzer.app.triage import moisture_screen  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path / "jobs", background=False))


def test_moisture_screen_finds_attenuated_stretch():
    rng = np.random.default_rng(1)
    n, ntr, dt = 400, 600, 0.1  # 40 ns window
    t = np.arange(n)[:, None] * dt
    decay = np.ones((1, ntr)) * 0.05
    decay[:, 300:360] = 0.25  # signal dies much faster here (wet ground)
    env = np.exp(-decay * t) + 0.01 * rng.random((n, ntr))
    out = moisture_screen(env, dt, 0.05, 0.1)
    assert out, "the attenuated stretch should be found"
    best = out[0]
    assert best["trace_start"] <= 310 and best["trace_end"] >= 350
    assert best["drop_db"] >= 6
    assert all(not (m["trace_end"] < 280 or m["trace_start"] > 380) for m in out), "nothing outside the stretch"


def test_moisture_screen_uniform_line_gives_nothing():
    rng = np.random.default_rng(2)
    t = np.arange(400)[:, None] * 0.1
    env = np.exp(-0.05 * t) * np.ones((1, 500)) + 0.01 * rng.random((400, 500))
    assert moisture_screen(env, 0.1, 0.05, 0.1) == []


def test_queue_and_decisions(client):
    j = client.post("/api/demo").json()
    jid = j["job_id"]
    q = client.get(f"/api/jobs/{jid}/queue.json").json()
    assert q["total"] == len(q["items"]) > 0
    kinds = {it["kind"] for it in q["items"]}
    assert "zone" in kinds and "audit" in kinds
    first = q["items"][0]
    assert first["kind"] != "audit", "audit picks come after the candidates"
    assert all(k in first for k in ("line_id", "lat", "lon", "distance_start_m", "target", "status"))
    # a zone decided through the normal review shows as decided in the queue
    z = next(it for it in q["items"] if it["kind"] == "zone")
    client.post(f"/api/jobs/{jid}/lines/{z['line_id']}/zones/{z['id']}/review", json={"action": "reject", "note": "ภาพใช้ไม่ได้"})
    q = client.get(f"/api/jobs/{jid}/queue.json").json()
    assert next(it for it in q["items"] if it["id"] == z["id"] and it["line_id"] == z["line_id"])["status"] == "unusable"
    # audit decision
    a = next(it for it in q["items"] if it["kind"] == "audit")
    r = client.post(f"/api/jobs/{jid}/lines/{a['line_id']}/screen/{a['id']}", json={"decision": "normal", "item": a})
    assert r.status_code == 200
    assert next(it for it in r.json()["items"] if it["id"] == a["id"])["status"] == "normal"
    assert client.post(f"/api/jobs/{jid}/lines/{a['line_id']}/screen/{a['id']}", json={"decision": "maybe"}).status_code == 400
    labels = [json.loads(x) for x in client.get(f"/api/jobs/{jid}/labels.jsonl").text.splitlines()]
    assert labels[-1]["action"] == "screen" and labels[-1]["after"]["decision"] == "normal"
