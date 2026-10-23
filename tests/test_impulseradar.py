"""Fixtures are built by hand from the public format description, not from real files."""

import numpy as np
import pytest

from gpr_analyzer.io.impulseradar import read_cor, read_impulseradar, read_mala

IPRH = """HEADER VERSION: 20
DATA VERSION: {version}
DATE: 2026-10-20
START TIME: 10:00:00
STOP TIME: 10:05:00
ANTENNA: 450 MHz
ANTENNA SEPARATION: 0.1700
SAMPLES: {samples}
SIGNAL POSITION: 0
CLIPPED SAMPLES: 0
RUNS: 0
MAX STACKS: 256
AUTOSTACKS: 1
FREQUENCY: {fs}
TIMEWINDOW: 64.0
LAST TRACE: {last}
TRIG SOURCE: wheel
TIME INTERVAL: 0.0000
DISTANCE INTERVAL: 0.050000
USER DISTANCE INTERVAL: 0.050000
STOP POSITION: 10.0
WHEEL NAME: Wheel
WHEEL CALIBRATION: 1000
ZERO LEVEL: 0
SOIL VELOCITY: 100
PREPROCESSING: 0
OPERATOR COMMENT:
ANTENNA F/W: 1.0
ANTENNA H/W: 1.0
ANTENNA FPGA: 1.0
ANTENNA SERIAL: 1
SOFTWARE VERSION: 1.0
POSITIONING: 0
CHANNELS: 1
CHANNEL CONFIGURATION: 0
CH_X_OFFSET: 0
CH_Y_OFFSET: 0
MEASUREMENT DIRECTION: 1
RELATIVE DIRECTION: 0
RELATIVE DISTANCE: 0
RELATIVE START: 0
"""


def make_ir(tmp_path, name="DAT_0001_A1", version=16, samples=8, traces=5, last=None, extra=0):
    rng = np.random.default_rng(0)
    dtype = "<i2" if version == 16 else "<i4"
    data = rng.integers(-3000, 3000, size=(traces, samples)).astype(dtype)
    (tmp_path / f"{name}.iprh").write_text(
        IPRH.format(version=version, samples=samples, fs=8000.0, last=last or traces)
    )
    raw = data.tobytes() + b"\x01\x00" * extra
    (tmp_path / f"{name}.iprb").write_bytes(raw)
    return data.T  # (samples, traces)


@pytest.mark.parametrize("version", [16, 32])
def test_reads_ir_data_versions(tmp_path, version):
    expected = make_ir(tmp_path, version=version)
    (r,) = read_impulseradar(tmp_path / "DAT_0001_A1.iprb")
    assert r.data.shape == (8, 5)
    np.testing.assert_array_equal(r.data, expected)
    assert r.dt_ns == pytest.approx(0.125)  # 8000 MHz sampling
    assert r.dx_m == pytest.approx(0.05)
    assert r.metadata["antenna"] == "450 MHz"


def test_trace_count_from_file_size_not_last_trace(tmp_path):
    make_ir(tmp_path, traces=7, last=3)
    (r,) = read_impulseradar(tmp_path / "DAT_0001_A1.iprh")
    assert r.n_traces == 7
    assert r.metadata["n_traces_from_header"] == 3


def test_partial_trace_dropped(tmp_path):
    make_ir(tmp_path, traces=4, extra=3)
    (r,) = read_impulseradar(tmp_path / "DAT_0001_A1.iprh")
    assert r.n_traces == 4 and r.metadata["partial_trace_dropped"]


def test_channel_suffix(tmp_path):
    make_ir(tmp_path, name="DAT_0002_A1_1")
    (r,) = read_impulseradar(tmp_path / "DAT_0002_A1_1.iprh")
    assert r.metadata["channel"] == 1 and r.metadata["channel_name"] == "low frequency"


def test_cor_attached(tmp_path):
    make_ir(tmp_path)
    (tmp_path / "DAT_0001_A1.cor").write_text(
        "1\t2026-10-20\t10:00:01\t13.7367\tN\t100.5231\tE\t5.20\tM\t1.2\n"
        "2\t2026-10-20\t10:00:02\t13.7368\tS\t100.5232\tW\t5.30\tM\t1.2\n"
    )
    (r,) = read_impulseradar(tmp_path / "DAT_0001_A1.iprb")
    gps = r.metadata["gps"]
    assert gps[0]["lat"] == pytest.approx(13.7367) and gps[1]["lon"] == pytest.approx(-100.5232)
    assert gps[0]["elevation"] == pytest.approx(5.2)


def test_mala_rd3(tmp_path):
    data = np.arange(30, dtype="<i2").reshape(6, 5)  # 6 traces x 5 samples
    (tmp_path / "LINE01.rad").write_text(
        "SAMPLES:5\nFREQUENCY:2000.0\nDISTANCE INTERVAL:0.02\nANTENNAS:500 MHz shielded\nLAST TRACE:6\n"
    )
    (tmp_path / "LINE01.rd3").write_bytes(data.tobytes())
    (r,) = read_mala(tmp_path / "LINE01.rad")
    np.testing.assert_array_equal(r.data, data.T)
    assert r.dt_ns == pytest.approx(0.5) and r.dx_m == pytest.approx(0.02)


def test_read_cor_skips_junk(tmp_path):
    p = tmp_path / "x.cor"
    p.write_text("garbage\n\n3\td\tt\t1\tN\t2\tE\n")
    assert read_cor(p) == [{"trace": 3, "date": "d", "time": "t", "lat": 1.0, "lon": 2.0}]


def test_measured_distance_interval_wins_over_user_setting(tmp_path):
    make_ir(tmp_path, traces=5)
    p = tmp_path / "DAT_0001_A1.iprh"
    p.write_text(p.read_text().replace("DISTANCE INTERVAL: 0.050000\nUSER", "DISTANCE INTERVAL: 0.018615\nUSER")
                 .replace("USER DISTANCE INTERVAL: 0.050000", "USER DISTANCE INTERVAL: 0.020000"))
    (r,) = read_impulseradar(p)
    assert r.dx_m == pytest.approx(0.018615)


def test_header_values_for_depth_and_time_zero(tmp_path):
    make_ir(tmp_path)
    p = tmp_path / "DAT_0001_A1.iprh"
    p.write_text(p.read_text().replace("ZERO LEVEL: 0", "ZERO LEVEL: 3"))
    (r,) = read_impulseradar(p)
    assert r.metadata["antenna_frequency_mhz"] == 450.0
    assert r.metadata["antenna_separation_m"] == pytest.approx(0.17)
    assert r.metadata["zero_level_sample"] == 3


def test_dual_channel_shares_line_cor(tmp_path):
    for ch in "01":
        make_ir(tmp_path, name=f"LINE_0001_{ch}")
    (tmp_path / "LINE_0001.cor").write_text("1\t2026-10-22\t10:00:00:000\t10.25\tN\t20.5\tE\t1.0\tM\t5\n")
    for ch in "01":
        (r,) = read_impulseradar(tmp_path / f"LINE_0001_{ch}.iprh")
        assert r.metadata["gps"][0]["lat"] == pytest.approx(10.25)


def test_markers_from_mrk(tmp_path):
    make_ir(tmp_path, name="LINE_0001_0")
    (tmp_path / "LINE_0001_0.mrk").write_text(
        "HEADER VERSION:300\n"
        "3\t5\tAPWA\t#EE3023\t2\t\t10.5\tN\t20.25\tE\t1.0\tM\t4\tN\t00000000-1111-2222-3333-444444444444\n"
        "junk\n"
    )
    (r,) = read_impulseradar(tmp_path / "LINE_0001_0.iprh")
    (m,) = r.metadata["markers"]
    assert (m["trace"], m["sample"], m["type"], m["colour"]) == (3, 5, "APWA", "#EE3023")
    assert m["lat"] == pytest.approx(10.5) and m["lon"] == pytest.approx(20.25)
    assert m["id"].startswith("00000000")


def test_empty_data_file_is_a_clear_error(tmp_path):
    make_ir(tmp_path)
    (tmp_path / "DAT_0001_A1.iprb").write_bytes(b"")
    with pytest.raises(ValueError, match="no complete trace"):
        read_impulseradar(tmp_path / "DAT_0001_A1.iprh")
