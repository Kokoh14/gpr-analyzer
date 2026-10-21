import struct

import numpy as np
import pytest

from gpr_analyzer import Radargram
from gpr_analyzer.io.segy import ibm_to_float, read_segy, write_segy


def test_ibm_float_known_values():
    # 0x42640000 = 100.0, 0xC2640000 = -100.0, 0x41100000 = 1.0, 0x00000000 = 0
    words = np.array([0x42640000, 0xC2640000, 0x41100000, 0], dtype=np.uint32)
    np.testing.assert_allclose(ibm_to_float(words), [100.0, -100.0, 1.0, 0.0])


def test_roundtrip(tmp_path):
    data = np.random.default_rng(3).normal(size=(20, 7)).astype(np.float32)
    r = Radargram(data, dt_ns=0.25, dx_m=0.05)
    write_segy(r, tmp_path / "a.sgy")
    (back,) = read_segy(tmp_path / "a.sgy")
    np.testing.assert_array_equal(back.data, data)
    assert back.dt_ns == pytest.approx(0.25)
    assert back.metadata["dt_unit_assumed"] == "ps"


def _segy(fmt, samples_be, dt, n_samples, n_traces, endian=">", ebcdic=False):
    text = ("C01 HAND MADE".ljust(80) * 40)
    textb = text.encode("cp500") if ebcdic else text.encode("ascii")
    binh = bytearray(400)
    struct.pack_into(f"{endian}hhh", binh, 16, dt, 0, n_samples)
    struct.pack_into(f"{endian}h", binh, 24, fmt)
    out = textb + bytes(binh)
    per = len(samples_be) // n_traces
    for i in range(n_traces):
        out += bytes(240) + samples_be[i * per : (i + 1) * per]
    return out


def test_ibm_int16_and_little_endian(tmp_path):
    ibm = np.array([0x42640000, 0x41100000] * 3, dtype=">u4").tobytes()
    p = tmp_path / "ibm.segy"
    p.write_bytes(_segy(1, ibm, 4, 2, 3, ebcdic=True))
    (r,) = read_segy(p, dt_unit="us")
    np.testing.assert_allclose(r.data[:, 0], [100.0, 1.0])
    assert r.dt_ns == pytest.approx(4000.0)
    assert read_segy(p)[0].dt_ns == pytest.approx(0.004)  # auto = picoseconds
    assert "HAND MADE" in r.metadata["text_header"]

    le = np.arange(8, dtype="<i2").tobytes()
    p2 = tmp_path / "le.segy"
    p2.write_bytes(_segy(3, le, 100, 4, 2, endian="<"))
    (r2,) = read_segy(p2, dt_unit="ps")
    assert r2.metadata["endian"] == "little"
    np.testing.assert_array_equal(r2.data, np.arange(8).reshape(2, 4).T)
    assert r2.dt_ns == pytest.approx(0.1)


def test_rejects_unknown_format(tmp_path):
    p = tmp_path / "x.sgy"
    p.write_bytes(_segy(99, b"", 1, 1, 1))
    with pytest.raises(ValueError):
        read_segy(p)
