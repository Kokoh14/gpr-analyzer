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
