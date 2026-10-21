"""Sensors & Software .HD / .DT1 reader (pulseEKKO, Noggin).

Layout (public pulseEKKO format description):
- .HD: text header, 'KEY = value' lines (NUMBER OF TRACES, NUMBER OF PTS/TRC, TOTAL TIME WINDOW, ...)
- .DT1: for each trace a 128-byte header (32 little-endian float32) then the samples
  (int16; bytes per sample is derived from the file size so int32 files also work)

Not yet validated against real files.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.io._util import parse_key_value_header, read_text, sibling, to_float

TRACE_HEADER_BYTES = 128
TRACE_HEADER_FIELDS = (
    "trace_number",
    "position",
    "n_points",
    "topography",
    "_unused",
    "bytes_per_point",
    "time_window",
    "n_stacks",
)


def read_dt1(path: str | Path) -> list[Radargram]:
    """Read a pulseEKKO profile from its .HD or .DT1 file."""
    path = Path(path)
    hd_path = path if path.suffix.lower() == ".hd" else sibling(path, ".hd")
    dt1_path = path if path.suffix.lower() == ".dt1" else sibling(path, ".dt1")
    header = parse_key_value_header(read_text(hd_path), sep="=")

    n_pts = int(to_float(header.get("NUMBER OF PTS/TRC")) or 0)
    if n_pts <= 0:
        raise ValueError(f"{hd_path}: NUMBER OF PTS/TRC missing")
    window = to_float(header.get("TOTAL TIME WINDOW"))
    if not window:
        raise ValueError(f"{hd_path}: TOTAL TIME WINDOW missing")

    raw = dt1_path.read_bytes()
    n_traces_hdr = int(to_float(header.get("NUMBER OF TRACES")) or 0)
    bytes_per_sample = 2
    if n_traces_hdr > 0 and len(raw) % n_traces_hdr == 0:
        stride = len(raw) // n_traces_hdr
        if (stride - TRACE_HEADER_BYTES) == 4 * n_pts:
            bytes_per_sample = 4
    stride = TRACE_HEADER_BYTES + n_pts * bytes_per_sample
    n_traces = len(raw) // stride

    buf = np.frombuffer(raw[: n_traces * stride], dtype=np.uint8).reshape(n_traces, stride)
    trace_headers = buf[:, :TRACE_HEADER_BYTES].copy().view("<f4")
    data = buf[:, TRACE_HEADER_BYTES:].copy().view(f"<i{bytes_per_sample}").T.astype(np.float32)

    positions = trace_headers[:, 1].astype(np.float64)
    step = to_float(header.get("STEP SIZE USED"))
    if not step and n_traces > 1:
        step = float(np.median(np.diff(positions))) or None

    meta = {
        "format": "sensoft_dt1",
        "source": str(dt1_path),
        "header": header,
        "antenna_frequency_mhz": to_float(header.get("NOMINAL FREQUENCY")),
        "antenna_separation_m": to_float(header.get("ANTENNA SEPARATION")),
        "time_zero_sample": to_float(header.get("TIMEZERO AT POINT")),
        "trace_positions": positions,
        "trace_headers": {name: trace_headers[:, i].copy() for i, name in enumerate(TRACE_HEADER_FIELDS) if not name.startswith("_")},
    }
    return [Radargram(data, dt_ns=window / (n_pts - 1), dx_m=step, metadata=meta)]


__all__ = ["read_dt1"]
