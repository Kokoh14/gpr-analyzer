"""GSSI .DZT reader (SIR-3000 / SIR-4000 / UtilityScan style files).

Layout used (public GSSI DZT description; readgssi is GPL so used only as a reference):
- one 1024-byte header block per channel, little endian
- data starts at 1024 * rh_data when rh_data < 1024, else at byte rh_data
- samples are unsigned for 8/16 bit (centred by subtracting 2**(bits-1)), signed for 32 bit
- channels interleaved trace by trace

Not yet validated against real files.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.io._util import C_M_PER_NS

HEADER_BLOCK = 1024


def _parse_header(block: bytes) -> dict:
    (tag, rh_data, nsamp, bits, zero) = struct.unpack_from("<HHHHh", block, 0)
    sps, spm, mpm, position, range_ns = struct.unpack_from("<5f", block, 10)
    (npass,) = struct.unpack_from("<H", block, 30)
    (nchan,) = struct.unpack_from("<H", block, 52)
    epsr, top, depth = struct.unpack_from("<3f", block, 54)
    antname = block[98:112].split(b"\x00")[0].decode("latin-1").strip()
    return {
        "rh_tag": tag,
        "rh_data": rh_data,
        "rh_nsamp": nsamp,
        "rh_bits": bits,
        "rh_zero": zero,
        "rhf_sps": sps,
        "rhf_spm": spm,
        "rhf_mpm": mpm,
        "rhf_position": position,
        "rhf_range": range_ns,
        "rh_npass": npass,
        "rh_nchan": nchan,
        "rhf_epsr": epsr,
        "rhf_top": top,
        "rhf_depth": depth,
        "rh_antname": antname,
    }


def read_dzt(path: str | Path) -> list[Radargram]:
    """Read a GSSI .DZT file. Returns one Radargram per channel."""
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < HEADER_BLOCK:
        raise ValueError(f"{path}: shorter than one DZT header block")
    h = _parse_header(raw[:HEADER_BLOCK])
    nchan = max(1, h["rh_nchan"])
    nsamp, bits = h["rh_nsamp"], h["rh_bits"]
    if bits not in (8, 16, 32) or nsamp <= 0:
        raise ValueError(f"{path}: unsupported DZT header (bits={bits}, samples={nsamp})")

    offset = HEADER_BLOCK * h["rh_data"] if h["rh_data"] < HEADER_BLOCK else h["rh_data"]
    if h["rh_data"] == 0:
        offset = HEADER_BLOCK * nchan
    dtype = {8: "<u1", 16: "<u2", 32: "<i4"}[bits]
    samples = np.frombuffer(raw, dtype=dtype, offset=offset)
    per_scan = nsamp * nchan
    n_scans = samples.size // per_scan
    samples = samples[: n_scans * per_scan].astype(np.float64)
    if bits in (8, 16):
        samples -= 2 ** (bits - 1)
    scans = samples.reshape(n_scans, nchan, nsamp)

    dt_ns = h["rhf_range"] / nsamp
    dx = 1.0 / h["rhf_spm"] if h["rhf_spm"] > 0 else None
    velocity = C_M_PER_NS / np.sqrt(h["rhf_epsr"]) if h["rhf_epsr"] > 0 else 0.1

    out = []
    for ch in range(nchan):
        ch_header = h if ch == 0 else _parse_header(raw[ch * HEADER_BLOCK : (ch + 1) * HEADER_BLOCK])
        meta = {
            "format": "gssi_dzt",
            "source": str(path),
            "header": ch_header,
            "antenna": ch_header["rh_antname"] or None,
            "channel": ch,
        }
        out.append(
            Radargram(
                scans[:, ch, :].T.astype(np.float32),
                dt_ns=dt_ns,
                dx_m=dx,
                velocity_m_per_ns=float(velocity),
                metadata=meta,
            )
        )
    return out


__all__ = ["read_dzt"]
