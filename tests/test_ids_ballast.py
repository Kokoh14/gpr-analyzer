"""IDS automatic ballast CSVs matched to a zone's traces and shown with the line."""

import time

import pytest

from gpr_analyzer.io import ids_ballast
from test_ids import make_zone

DX = 0.36


def write_csvs(folder, parts, x0=22210.0, channel=1):
    folder.mkdir(parents=True)
    t = 0
    for k, n in enumerate(parts, start=1):
        rows = ["X1[m];X2[m];Moisture at the base of ballast;Ballast moisture;Ballast fouling;Undulation of Ballast"]
        x = x0 + (t + 1) * DX
        end = x0 + (t + n) * DX
        i = 0
        while x + 4.3 <= end:
            rows.append(f"{x:.2f};{x + 4.32:.2f};{255 if i % 3 else 240};255;{k};{i % 3};")
            x += 5.04
            i += 1
        (folder / f"LI{channel:02d}{k:04d}_automatic.csv").write_text("\n".join(rows) + "\n")
        t += n


@pytest.fixture()
def data(tmp_path):
    make_zone(tmp_path / "JOB.MIS" / "North_2221to2239_ob.ZON", parts=(300, 200))
    write_csvs(tmp_path / "csv" / "22 to 23" / "ANT 1", (300, 200))
    write_csvs(tmp_path / "csv" / "other km" / "ANT 1", (300, 120), x0=50000.0)  # same names, other zone
    return tmp_path


def test_zone_matched_by_part_lengths(data):
    idx = ids_ballast.index(data / "csv")
    rows, folder = ids_ballast.zone_rows(idx, 1, [300, 200], DX)
    assert folder.parent.name == "22 to 23"
    assert rows[0][:2] == [1, 13] and rows[0][2:] == [240, 255, 1, 0]
    assert all(r[4] == 2 for r in rows if r[0] >= 300) and rows[-1][1] < 500
    assert ids_ballast.zone_rows(idx, 1, [300, 260], DX) == ([], None)  # no folder fits
    assert ids_ballast.zone_rows(idx, 2, [300, 200], DX) == ([], None)  # no CSVs for channel 2
    cropped = ids_ballast.crop_rows(rows, 290, 320)
    assert cropped[0][0] == 0 and all(r[1] <= 30 for r in cropped)


def test_app_line_carries_ids_classes(data):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient

    from gpr_analyzer.app.server import create_app

    c = TestClient(create_app(data / "jobs"))
    folder = data / "JOB.MIS"
    assert c.post("/api/jobs/from-folder", json={"path": str(folder), "recursive": True,
                                                 "ids_ballast_dir": str(data / "nope")}).status_code == 400
    jid = c.post("/api/jobs/from-folder", json={"path": str(folder), "recursive": True,
                                                "ranges": {str(folder / "North_2221to2239_ob.ZON" / "LI010001.dt"): [36, 144]},
                                                "ids_ballast_dir": str(data / "csv")}).json()["job_id"]
    t = time.monotonic()
    while c.get(f"/api/jobs/{jid}/progress").json()["state"] != "done":
        assert time.monotonic() - t < 120
        time.sleep(0.2)
    j = c.get(f"/api/jobs/{jid}").json()
    ch1, ch2 = j["lines"][0], j["lines"][1]
    assert ch1["trace_offset"] == 100 and ch1["ids_ballast"] and ch1["ids_ballast"][0][0] == 0
    assert max(r[1] for r in ch1["ids_ballast"]) <= ch1["n_traces"] - 1
    assert ch2["ids_ballast"] is None  # only channel 1 has CSVs
    assert "ไม่พบ Ballast CSV" in c.get(f"/api/jobs/{jid}/log.txt").text
