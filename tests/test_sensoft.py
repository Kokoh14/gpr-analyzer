"""Fixtures built from the public pulseEKKO format description, not from real files."""

import numpy as np
import pytest

from gpr_analyzer.io.sensoft import read_dt1

HD = """1234
Data Collected with pulseEKKO
2026-10-20
NUMBER OF TRACES   = {n}
NUMBER OF PTS/TRC  = {pts}
TIMEZERO AT POINT  = 3
TOTAL TIME WINDOW  = {win}
STARTING POSITION  = 0.000
FINAL POSITION     = 1.000
STEP SIZE USED     = 0.250
POSITION UNITS     = m
NOMINAL FREQUENCY  = 250.00
ANTENNA SEPARATION = 0.38
PULSER VOLTAGE (V) = 400
NUMBER OF STACKS   = 4
"""


def make_dt1(tmp_path, n=5, pts=9, dtype="<i2"):
    rng = np.random.default_rng(2)
    data = rng.integers(-1000, 1000, size=(n, pts)).astype(dtype)
    (tmp_path / "LINE00.HD").write_text(HD.format(n=n, pts=pts, win=40.0))
    chunks = []
    for i in range(n):
        th = np.zeros(32, dtype="<f4")
        th[0], th[1], th[2], th[5], th[6] = i + 1, i * 0.25, pts, np.dtype(dtype).itemsize, 40.0
        chunks.append(th.tobytes() + data[i].tobytes())
    (tmp_path / "LINE00.DT1").write_bytes(b"".join(chunks))
    return data.T


@pytest.mark.parametrize("dtype", ["<i2", "<i4"])
def test_reads_dt1(tmp_path, dtype):
    expected = make_dt1(tmp_path, dtype=dtype)
    (r,) = read_dt1(tmp_path / "LINE00.DT1")
    np.testing.assert_array_equal(r.data, expected)
    assert r.dt_ns == pytest.approx(5.0)  # 40 ns over 9 points
    assert r.dx_m == pytest.approx(0.25)
    assert r.metadata["antenna_frequency_mhz"] == 250.0
    np.testing.assert_allclose(r.metadata["trace_positions"], [0, 0.25, 0.5, 0.75, 1.0])


def test_missing_header_field(tmp_path):
    (tmp_path / "A.HD").write_text("NUMBER OF TRACES = 1\n")
    (tmp_path / "A.DT1").write_bytes(b"")
    with pytest.raises(ValueError):
        read_dt1(tmp_path / "A.HD")
