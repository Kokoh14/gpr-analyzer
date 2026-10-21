"""SEG-Y (rev 0/1) reader and writer for GPR profiles.

- 3200-byte text header (EBCDIC or ASCII), 400-byte binary header, optional extended text headers,
  then traces of 240-byte header + samples
- big endian per the standard; little-endian files are detected from the format code
- sample formats: 1 IBM float, 2 int32, 3 int16, 5 IEEE float, 8 int8

GPR software stores the sample interval inconsistently: the standard unit is microseconds but GPR
intervals are well below 1 us (the smallest integer the field can hold), so GPR programs write picoseconds
instead. `dt_unit="auto"` therefore reads the field as picoseconds; pass dt_unit="us" for a true seismic file.
Which unit SubScan or the user's other software writes is still to be confirmed with a real export.

Not yet validated against SEG-Y exported by SubScan or other GPR software.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram

TEXT_BYTES = 3200
BIN_BYTES = 400
TRACE_HEADER_BYTES = 240
_FORMATS = {1: 4, 2: 4, 3: 2, 5: 4, 8: 1}


def ibm_to_float(words: np.ndarray) -> np.ndarray:
    """Convert big-endian-decoded uint32 IBM System/360 floats to float64."""
    words = words.astype(np.uint32)
    sign = np.where(words >> 31, -1.0, 1.0)
    exponent = ((words >> 24) & 0x7F).astype(np.int32) - 64
    mantissa = (words & 0x00FFFFFF).astype(np.float64) / float(1 << 24)
    return sign * mantissa * np.power(16.0, exponent)


def _decode_text(block: bytes) -> str:
    if sum(1 for c in block[:80] if c == 0x40 or 0xC1 <= c <= 0xF9) > 40:
        return block.decode("cp500", errors="replace")
    return block.decode("latin-1", errors="replace")


def read_segy(path: str | Path, dt_unit: str = "auto") -> list[Radargram]:
    """Read a SEG-Y file into one Radargram. dt_unit: 'auto', 'us' or 'ps'."""
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < TEXT_BYTES + BIN_BYTES:
        raise ValueError(f"{path}: too short for SEG-Y")
    binh = raw[TEXT_BYTES : TEXT_BYTES + BIN_BYTES]

    endian = ">"
    fmt = struct.unpack_from(">h", binh, 24)[0]
    if fmt not in _FORMATS:
        fmt_le = struct.unpack_from("<h", binh, 24)[0]
        if fmt_le not in _FORMATS:
            raise ValueError(f"{path}: unsupported sample format code {fmt}")
        endian, fmt = "<", fmt_le

    dt_raw, _, n_samples = struct.unpack_from(f"{endian}hhh", binh, 16)
    n_ext = struct.unpack_from(f"{endian}h", binh, 304)[0]
    offset = TEXT_BYTES + BIN_BYTES + max(0, n_ext) * TEXT_BYTES

    first_th = raw[offset : offset + TRACE_HEADER_BYTES]
    th_ns, th_dt = struct.unpack_from(f"{endian}Hh", first_th, 114)
    n_samples = n_samples if n_samples > 0 else th_ns
    dt_raw = dt_raw if dt_raw > 0 else th_dt
    if n_samples <= 0 or dt_raw <= 0:
        raise ValueError(f"{path}: sample count or interval missing")

    if dt_unit == "auto":
        dt_unit = "ps"
    if dt_unit not in ("ps", "us"):
        raise ValueError("dt_unit must be 'auto', 'ps' or 'us'")
    dt_ns = dt_raw / 1000.0 if dt_unit == "ps" else dt_raw * 1000.0

    nbytes = _FORMATS[fmt]
    stride = TRACE_HEADER_BYTES + n_samples * nbytes
    n_traces = (len(raw) - offset) // stride
    buf = np.frombuffer(raw[offset : offset + n_traces * stride], dtype=np.uint8).reshape(n_traces, stride)
    body = buf[:, TRACE_HEADER_BYTES:].copy()
    if fmt == 1:
        data = ibm_to_float(body.view(f"{endian}u4"))
    else:
        dtype = {2: "i4", 3: "i2", 5: "f4", 8: "i1"}[fmt]
        data = body.view(f"{endian}{dtype}")
    headers = buf[:, :TRACE_HEADER_BYTES].copy()
    cdp_x = headers[:, 180:184].copy().view(f"{endian}i4").ravel()
    cdp_y = headers[:, 184:188].copy().view(f"{endian}i4").ravel()
    dx_m = _trace_spacing(headers, cdp_x, cdp_y, endian)

    meta = {
        "format": "segy",
        "source": str(path),
        "text_header": _decode_text(raw[:TEXT_BYTES]),
        "sample_format": fmt,
        "endian": "big" if endian == ">" else "little",
        "dt_unit_assumed": dt_unit,
        "cdp_x": cdp_x,
        "cdp_y": cdp_y,
    }
    if dx_m is not None:
        meta["dx_from"] = "CDP coordinates"
    return [Radargram(data.T.astype(np.float32), dt_ns=dt_ns, dx_m=dx_m, metadata=meta)]


def _trace_spacing(headers: np.ndarray, cdp_x: np.ndarray, cdp_y: np.ndarray, endian: str) -> float | None:
    """Median step between CDP coordinates when they are lengths (coordinate units 1 or unset) and
    move along the line; None otherwise (e.g. arc-seconds, or every trace at the same point)."""
    if len(cdp_x) < 2:
        return None
    scalar = int(headers[0, 70:72].copy().view(f"{endian}i2")[0])
    units = int(headers[0, 88:90].copy().view(f"{endian}i2")[0])
    if units not in (0, 1):
        return None
    scale = float(scalar) if scalar > 0 else (1.0 / -scalar if scalar < 0 else 1.0)
    step = np.hypot(np.diff(cdp_x.astype(np.float64)), np.diff(cdp_y.astype(np.float64))) * scale
    dx = float(np.median(step))
    return dx if dx > 0 and np.isfinite(dx) else None


def write_segy(radargram: Radargram, path: str | Path) -> None:
    """Write big-endian IEEE-float SEG-Y. The sample interval is stored in picoseconds (noted in the text header)."""
    path = Path(path)
    r = radargram
    lines = [
        "GPR-ANALYZER SEG-Y EXPORT",
        "SAMPLE INTERVAL IN PICOSECONDS (GPR CONVENTION), FORMAT 5 IEEE FLOAT",
        f"SAMPLES {r.n_samples} TRACES {r.n_traces} DT_NS {r.dt_ns:g}",
        f"DX_M {r.dx_m if r.dx_m is not None else 'unknown'} VELOCITY_M_PER_NS {r.velocity_m_per_ns:g}",
    ]
    text = "".join(f"C{i + 1:02d} {line}".ljust(80)[:80] for i, line in enumerate(lines)).ljust(TEXT_BYTES)
    dt_ps = int(round(r.dt_ns * 1000))
    if not 0 < dt_ps < 32768 or r.n_samples >= 32768:
        raise ValueError("sample interval or count does not fit SEG-Y rev1 16-bit fields")
    binh = bytearray(BIN_BYTES)
    struct.pack_into(">hhh", binh, 16, dt_ps, 0, r.n_samples)
    struct.pack_into(">h", binh, 24, 5)
    struct.pack_into(">h", binh, 28, 1)  # trace sorting: as recorded
    struct.pack_into(">H", binh, 300, 0x0100)  # rev 1
    struct.pack_into(">h", binh, 302, 1)  # fixed trace length
    with path.open("wb") as f:
        f.write(text.encode("ascii"))
        f.write(binh)
        for i in range(r.n_traces):
            th = bytearray(TRACE_HEADER_BYTES)
            struct.pack_into(">ii", th, 0, i + 1, i + 1)
            if r.dx_m:  # position along the line in mm (scalar -1000) so the reader recovers dx
                struct.pack_into(">h", th, 70, -1000)
                struct.pack_into(">i", th, 180, int(round(i * r.dx_m * 1000)))
                struct.pack_into(">h", th, 88, 1)
            struct.pack_into(">Hh", th, 114, r.n_samples, dt_ps)
            f.write(th)
            f.write(r.data[:, i].astype(">f4").tobytes())


__all__ = ["read_segy", "write_segy", "ibm_to_float"]
