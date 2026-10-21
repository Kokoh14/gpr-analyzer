"""DZT fixtures are built byte by byte from the public header description, not from real files."""

import struct

import numpy as np
import pytest

from gpr_analyzer.io.gssi import read_dzt


def dzt_header(nsamp, bits, nchan=1, range_ns=50.0, spm=20.0, epsr=9.0, ant="3207"):
    b = bytearray(1024)
    struct.pack_into("<HHHHh", b, 0, 0x00FF, 1024 * nchan, nsamp, bits, 2)
    struct.pack_into("<5f", b, 10, 64.0, spm, 1.0, 0.0, range_ns)
    struct.pack_into("<H", b, 52, nchan)
    struct.pack_into("<3f", b, 54, epsr, 0.0, 2.5)
    b[98 : 98 + len(ant)] = ant.encode()
    return bytes(b)


def test_dzt_16bit_single_channel(tmp_path):
    traces = np.random.default_rng(1).integers(0, 65535, size=(4, 10)).astype("<u2")
    p = tmp_path / "FILE____001.DZT"
    p.write_bytes(dzt_header(10, 16) + traces.tobytes())
    (r,) = read_dzt(p)
    np.testing.assert_array_equal(r.data, traces.T.astype(np.float64) - 32768)
    assert r.dt_ns == pytest.approx(5.0)
    assert r.dx_m == pytest.approx(0.05)
    assert r.velocity_m_per_ns == pytest.approx(0.299792458 / 3)
    assert r.metadata["antenna"] == "3207"


def test_dzt_two_channels_interleaved(tmp_path):
    nsamp = 6
    ch0 = np.full((3, nsamp), 10, dtype="<i4")
    ch1 = np.full((3, nsamp), -7, dtype="<i4")
    body = b"".join(ch0[i].tobytes() + ch1[i].tobytes() for i in range(3))
    hdr = dzt_header(nsamp, 32, nchan=2)
    p = tmp_path / "two.dzt"
    p.write_bytes(hdr + hdr + body)
    a, b = read_dzt(p)
    assert a.data.shape == (6, 3) and (a.data == 10).all() and (b.data == -7).all()
    assert b.metadata["channel"] == 1


def test_dzt_rejects_bad_bits(tmp_path):
    p = tmp_path / "bad.dzt"
    p.write_bytes(dzt_header(10, 12))
    with pytest.raises(ValueError):
        read_dzt(p)
