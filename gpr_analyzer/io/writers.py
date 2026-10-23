"""Writers for the formats the readers understand, so a synthetic (or processed) radargram can be
saved as an ImpulseRadar, MALA, GSSI, pulseEKKO or SEG-Y file and read back.

Used by the end-to-end tests. The files follow the same public layouts as the readers; they are
not checked against vendor software, so do not hand them to a vendor program as proof of anything.

Integer formats are scaled to use most of the int16 range; amplitudes are relative anyway.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.io._util import C_M_PER_NS
from gpr_analyzer.io.segy import write_segy


def _int16(data: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(data))) or 1.0
    return np.round(data * (30000.0 / peak)).astype(np.int16)


def _antenna_mhz(r: Radargram) -> float | None:
    f = r.metadata.get("antenna_frequency_mhz")
    return float(f) if f else None


def _key_value_header(r: Radargram, extra: dict[str, str]) -> str:
    """CrossOver/MALA style 'KEY: value' header."""
    f = _antenna_mhz(r)
    rows = {
        **extra,
        "SAMPLES": str(r.n_samples),
        "FREQUENCY": f"{1000.0 / r.dt_ns:.6f}",  # sampling frequency in MHz
        "TIMEWINDOW": f"{r.dt_ns * r.n_samples:.6f}",
        "LAST TRACE": str(r.n_traces),
        "DISTANCE INTERVAL": f"{r.dx_m or 0.0:.6f}",
        "USER DISTANCE INTERVAL": f"{r.dx_m or 0.0:.6f}",
        "ANTENNA": f"{f:g} MHz" if f else "unknown",
        "SOIL VELOCITY": f"{r.velocity_m_per_ns * 1000.0:g}",  # m/us
        "CHANNELS": "1",
    }
    return "".join(f"{k}: {v}\n" for k, v in rows.items())


def write_impulseradar(r: Radargram, path: str | Path) -> Path:
    """Write <stem>.iprh + <stem>.iprb (DATA VERSION 16). Returns the .iprb path."""
    path = Path(path)
    path.with_suffix(".iprh").write_text(_key_value_header(r, {"HEADER VERSION": "20", "DATA VERSION": "16"}))
    out = path.with_suffix(".iprb")
    out.write_bytes(_int16(r.data).T.astype("<i2").tobytes())
    return out


def write_mala(r: Radargram, path: str | Path) -> Path:
    """Write <stem>.rad + <stem>.rd3 (int16). Returns the .rd3 path."""
    path = Path(path)
    path.with_suffix(".rad").write_text(_key_value_header(r, {}))
    out = path.with_suffix(".rd3")
    out.write_bytes(_int16(r.data).T.astype("<i2").tobytes())
    return out


def write_dzt(r: Radargram, path: str | Path) -> Path:
    """Write a single-channel 16-bit GSSI .DZT (velocity stored as rhf_epsr)."""
    path = Path(path)
    b = bytearray(1024)
    struct.pack_into("<HHHHh", b, 0, 0x00FF, 1024, r.n_samples, 16, 2)
    spm = 1.0 / r.dx_m if r.dx_m else 0.0
    struct.pack_into("<5f", b, 10, 64.0, spm, 1.0, 0.0, r.dt_ns * r.n_samples)
    struct.pack_into("<H", b, 52, 1)
    epsr = (C_M_PER_NS / r.velocity_m_per_ns) ** 2
    struct.pack_into("<3f", b, 54, epsr, 0.0, r.n_samples * r.dt_ns * r.velocity_m_per_ns / 2.0)
    f = _antenna_mhz(r)
    name = f"{f:g}MHz".encode() if f else b""
    b[98 : 98 + min(len(name), 14)] = name[:14]
    samples = (_int16(r.data).astype(np.int32) + 32768).astype("<u2")
    path.write_bytes(bytes(b) + samples.T.tobytes())
    return path


def write_dt1(r: Radargram, path: str | Path) -> Path:
    """Write <stem>.hd + <stem>.dt1 (int16, 128-byte trace headers). Returns the .dt1 path."""
    path = Path(path)
    dx = r.dx_m or 1.0
    window = r.dt_ns * (r.n_samples - 1)  # the reader uses dt = window / (points - 1)
    f = _antenna_mhz(r)
    hd = (
        "1234\nData written by gpr-analyzer\n\n"
        f"NUMBER OF TRACES   = {r.n_traces}\n"
        f"NUMBER OF PTS/TRC  = {r.n_samples}\n"
        "TIMEZERO AT POINT  = 1\n"
        f"TOTAL TIME WINDOW  = {window:.6f}\n"
        "STARTING POSITION  = 0.000\n"
        f"FINAL POSITION     = {(r.n_traces - 1) * dx:.6f}\n"
        f"STEP SIZE USED     = {dx:.6f}\n"
        "POSITION UNITS     = m\n"
        f"NOMINAL FREQUENCY  = {f or 0:.2f}\n"
        "ANTENNA SEPARATION = 0.00\n"
        "NUMBER OF STACKS   = 1\n"
    )
    path.with_suffix(".hd").write_text(hd)
    data = _int16(r.data)
    out = path.with_suffix(".dt1")
    with out.open("wb") as fh:
        for i in range(r.n_traces):
            th = np.zeros(32, dtype="<f4")
            th[:8] = (i + 1, i * dx, r.n_samples, 0.0, 0.0, 2.0, window, 1.0)
            fh.write(th.tobytes())
            fh.write(data[:, i].astype("<i2").tobytes())
    return out


WRITERS = {
    ".iprb": write_impulseradar,
    ".iprh": write_impulseradar,
    ".rd3": write_mala,
    ".rad": write_mala,
    ".dzt": write_dzt,
    ".dt1": write_dt1,
    ".hd": write_dt1,
    ".sgy": write_segy,
    ".segy": write_segy,
}


def write(r: Radargram, path: str | Path) -> Path:
    """Write `r` in the format given by the extension; returns the file to pass to io.read."""
    path = Path(path)
    writer = WRITERS.get(path.suffix.lower())
    if writer is None:
        raise ValueError(f"{path.name}: no writer for {path.suffix!r}; supported: {', '.join(sorted(WRITERS))}")
    out = writer(r, path)
    return Path(out) if out is not None else path


__all__ = ["WRITERS", "write", "write_dt1", "write_dzt", "write_impulseradar", "write_mala", "write_segy"]
