"""IDS zone reader (.ZON folder with LIccpppp.dt parts + LI01pppp.GPS) and its use in the app."""

import time
from pathlib import Path

import numpy as np
import pytest

from gpr_analyzer.app.files import sort_files
from gpr_analyzer.io import read
from gpr_analyzer.io.ids import read_gga, read_ids, read_ids_header

REC = 4 + 2 * 128  # tag + 128 int16 samples
GPS_EVERY = 10


def _rec(tag: bytes, body: bytes = b"") -> bytes:
    return (tag.ljust(4, b"\0") + body).ljust(REC, b"\0")[:REC]


def write_part(path: Path, data: np.ndarray, tx_x: float, rx_x: float, gps_flags: bool, zone="Z") -> None:
    """Same record layout as the field files: V, I, C, ATR, H header records then one 'R' record per trace."""
    n_samples, n_traces = data.shape
    ints = np.array([REC, n_traces, n_samples, n_traces, n_samples, 1, 1, 1, 1, 30, 0], "<i4").tobytes()
    vals = [2.5e8, 1e8, 8e-8, 8e-8, 0.0, 3.84e-3, 0.36, 0.012, 0.36, 7.8125e-11]
    recs = [_rec(b"V\x04", np.array([REC], "<i4").tobytes()),
            _rec(b"I", zone.encode()), _rec(b"C", b"06/15/22  05:32:05"),
            _rec(b"ATR", f"{tx_x:16.6E}{0:16.6E}{90:16.6E}  400{rx_x:16.6E}{0:16.6E}{90:16.6E}  400".encode()),
            _rec(b"H\x15\x04", ints + "".join(f"{v:16.6E}" for v in vals).encode())]
    for i in range(n_traces):
        flag = 7 if gps_flags and i % GPS_EVERY == 0 else 0
        recs.append(bytes([ord("R"), flag, 0, 0]) + data[:, i].astype("<i2").tobytes())
    path.write_bytes(b"".join(recs))


def write_gps(path: Path, n: int, lat0: float, i0: int) -> None:
    lines = []
    for k in range(n):
        lat = lat0 + (i0 + k) * GPS_EVERY * 0.36 / 111320  # due north, one fix per GPS_EVERY traces
        lines.append(f"$GPGGA,0324{k % 60:02d}.00,{int(lat):02d}{(lat % 1) * 60:08.5f},N,10035.53225,E,4,10,0.9,8.2,M,-27.8,M,,*75")
    lines.insert(2, "$GPGGA,032500.00,,,,,0,0,,,M,,M,,*66")  # no fix: still one GGA line per flagged trace
    path.write_text("\n".join(lines) + "\n")


def make_zone(folder: Path, parts=(120, 60), channels=(1, 2, 3)) -> Path:
    folder.mkdir(parents=True)
    rng = np.random.default_rng(1)
    t0 = 0
    for p, n in enumerate(parts, start=1):
        for ch in channels:
            d = (rng.normal(0, 50, (128, n)) + 1000 * ch).astype(np.int16)
            d[:, 0] = t0 + np.arange(128)  # trace number marker to check the join order
            write_part(folder / f"LI{ch:02d}{p:04d}.dt", d, tx_x=-0.45 + 0.5 * (ch - 1), rx_x=-0.55 + 0.5 * (ch - 1),
                       gps_flags=ch == 1, zone=folder.stem)
        n_flag = (n + GPS_EVERY - 1) // GPS_EVERY
        write_gps(folder / f"LI01{p:04d}.GPS", n_flag - 1, 13.9, t0 // GPS_EVERY)
        t0 += n
    (folder / "Config.txt").write_text("[CONFIGURATION]\nChannelNumbers = 4\n")
    (folder / "Camera").mkdir()
    (folder / "Camera" / "0001.jpg").write_bytes(b"\xff\xd8")
    return folder


@pytest.fixture()
def zone(tmp_path):
    return make_zone(tmp_path / "JOB.MIS" / "North_0140to10500_ob.ZON")


def test_read_joins_parts_per_channel(zone):
    rgs = read(zone / "LI020002.dt")
    assert [r.metadata["channel"] for r in rgs] == [1, 2, 3]
    r = rgs[1]
    assert r.data.shape == (128, 180) and r.dt_ns == pytest.approx(80 / 128) and r.dx_m == pytest.approx(0.36)
    assert r.metadata["parts"] == ["LI020001.dt", "LI020002.dt"] and r.metadata["zone"] == "North_0140to10500_ob"
    assert r.data[5, 0] == 5 and r.data[5, 120] == 125  # part 2 follows part 1
    assert r.metadata["antenna_frequency_mhz"] == 400 and r.metadata["antenna_separation_m"] == pytest.approx(0.1)
    assert [x.metadata["cross_offset_m"] for x in rgs] == pytest.approx([-0.5, 0.0, 0.5])


def test_gps_fix_goes_to_its_flagged_trace(zone):
    gps = read_ids(zone / "LI010001.dt")[0].metadata["gps"]
    # part 1: flagged traces 0,10,...,110 (12) but GPS has 11 lines + 1 no-fix line at index 2 -> skipped
    trs = [g["trace"] for g in gps]
    assert trs[:4] == [0, 10, 30, 40] and 120 in trs  # part 2 starts at trace 120
    assert all(b["lat"] > a["lat"] for a, b in zip(gps, gps[1:])) and gps[0]["quality"] == 4
    assert len(read_gga(zone / "LI010001.GPS")) == 12


def test_header_only_scan(zone):
    h = read_ids_header(zone / "LI010002.dt")
    assert h["n_traces"] == 60 and h["n_samples"] == 128 and h["window_ns"] == pytest.approx(80)


def test_wrong_record_layout_is_refused(zone):
    p = zone / "LI030001.dt"
    raw = bytearray(p.read_bytes())
    raw[4 * REC + 4 + 8:4 * REC + 4 + 12] = np.array([256], "<i4").tobytes()  # header says 256 samples
    p.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="samples"):
        read_ids(p)


def test_zone_is_one_line_with_folder_name(zone, tmp_path):
    files = [p for p in zone.parent.rglob("*") if p.is_file()]
    s = sort_files(files)
    (g,) = s.groups
    assert g.family == "ids" and g.stem == "North_0140to10500_ob" and g.primary.name == "LI010001.dt"
    assert len(g.members) == 6 and {p.name for p in g.companions} >= {"Config.txt", "LI010001.GPS"}
    assert not s.unassigned  # camera pictures inside the zone are left out


def test_scan_and_app_job(zone):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient

    from gpr_analyzer.app import folders
    from gpr_analyzer.app.server import create_app

    survey = zone.parent
    r = folders.scan_folder(survey, recursive=True)
    (ln,) = r["lines"]
    assert ln["family"] == "ids" and ln["channels"] == 3 and ln["parts"] == 2 and ln["n_traces"] == 180
    assert ln["length_m"] == pytest.approx(64.8) and ln["gps_fixes"] == 16 and ln["track"]
    assert abs(ln["length_gps_m"] - ln["length_m"]) < 10

    c = TestClient(create_app(survey.parent / "jobs"))
    jid = c.post("/api/jobs/from-folder", json={"path": str(survey), "recursive": True,
                                                "include": [ln["primary"]]}).json()["job_id"]
    t = time.monotonic()
    while c.get(f"/api/jobs/{jid}/progress").json()["state"] != "done":
        assert time.monotonic() - t < 120
        time.sleep(0.2)
    j = c.get(f"/api/jobs/{jid}").json()
    assert [x["line_id"] for x in j["lines"]] == ["L1-ch1", "L1-ch2", "L1-ch3"]
    assert "North_0140to10500_ob" in c.get(f"/api/jobs/{jid}/log.txt").text
