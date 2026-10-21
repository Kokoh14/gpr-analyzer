"""ImpulseRadar (.iprh / .iprb) and MALA (.rad / .rd3 / .rd7) readers.

Both vendors use the same layout (public spec "CrossOver Data Files Formats" v1.1 and MALA docs):
- header: text file, one 'KEY: value' per line
- data: raw little-endian integers, no trace headers, traces stored one after another
- trace count is taken from the file size, not from LAST TRACE (which can be stale)
ImpulseRadar dual-channel units write two pairs of files, suffix _0 (high frequency) and _1 (low).

Not yet validated against real files from the user's unit.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.io._util import parse_key_value_header, read_text, sibling, to_float

_CHANNEL_NAMES = {"0": "high frequency", "1": "low frequency"}


def _read_pair(header_path: Path, data_path: Path, bytes_per_sample: int, fmt: str) -> Radargram:
    header = parse_key_value_header(read_text(header_path))
    n_samples = int(to_float(header.get("SAMPLES")) or 0)
    if n_samples <= 0:
        raise ValueError(f"{header_path}: SAMPLES missing or zero")

    raw = np.fromfile(data_path, dtype=f"<i{bytes_per_sample}")
    n_traces, rest = divmod(raw.size, n_samples)
    if rest:
        # Partial last trace (e.g. recording stopped mid-write): drop it, keep the note.
        raw = raw[: n_traces * n_samples]
    data = raw.reshape(n_traces, n_samples).T.astype(np.float32)

    # FREQUENCY is the sampling frequency in MHz.
    fs_mhz = to_float(header.get("FREQUENCY"))
    window_ns = to_float(header.get("TIMEWINDOW"))
    if fs_mhz:
        dt_ns = 1000.0 / fs_mhz
    elif window_ns:
        dt_ns = window_ns / n_samples
    else:
        raise ValueError(f"{header_path}: neither FREQUENCY nor TIMEWINDOW given")

    dx = to_float(header.get("USER DISTANCE INTERVAL")) or to_float(header.get("DISTANCE INTERVAL"))
    if dx is not None and dx <= 0:
        dx = None

    meta = {
        "format": fmt,
        "source": str(data_path),
        "header": header,
        "antenna": header.get("ANTENNA") or header.get("ANTENNAS"),
        "n_traces_from_header": to_float(header.get("LAST TRACE")),
        "partial_trace_dropped": bool(rest),
    }
    stem = data_path.stem
    if fmt == "impulseradar" and len(stem) > 2 and stem[-2] == "_" and stem[-1] in _CHANNEL_NAMES:
        meta["channel"] = int(stem[-1])
        meta["channel_name"] = _CHANNEL_NAMES[stem[-1]]
    cor = data_path.with_suffix(".cor")
    if cor.exists():
        meta["gps"] = read_cor(cor)
    return Radargram(data, dt_ns=dt_ns, dx_m=dx, metadata=meta)


def read_impulseradar(path: str | Path) -> list[Radargram]:
    """Read one ImpulseRadar profile from its .iprh or .iprb file."""
    path = Path(path)
    header_path = path if path.suffix.lower() == ".iprh" else sibling(path, ".iprh")
    data_path = path if path.suffix.lower() == ".iprb" else sibling(path, ".iprb")
    header = parse_key_value_header(read_text(header_path))
    version = int(to_float(header.get("DATA VERSION")) or 16)
    if version not in (16, 32):
        raise ValueError(f"{header_path}: unsupported DATA VERSION {version}")
    return [_read_pair(header_path, data_path, version // 8, "impulseradar")]


def read_mala(path: str | Path) -> list[Radargram]:
    """Read a MALA profile: .rad header + .rd3 (int16) or .rd7 (int32) data."""
    path = Path(path)
    header_path = path if path.suffix.lower() == ".rad" else sibling(path, ".rad")
    if path.suffix.lower() in (".rd3", ".rd7"):
        data_path = path
    else:
        try:
            data_path = sibling(path, ".rd3")
        except FileNotFoundError:
            data_path = sibling(path, ".rd7")
    nbytes = 2 if data_path.suffix.lower() == ".rd3" else 4
    return [_read_pair(header_path, data_path, nbytes, "mala")]


def read_cor(path: str | Path) -> list[dict]:
    """Read a .cor GPS file: tab-separated trace, date, time, lat, N/S, lon, E/W, elevation, ...

    Latitude/longitude are returned as signed decimal degrees.
    """
    rows = []
    for line in read_text(Path(path)).splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            trace = int(float(parts[0]))
            lat = float(parts[3]) * (-1 if parts[4].upper() == "S" else 1)
            lon = float(parts[5]) * (-1 if parts[6].upper() == "W" else 1)
        except ValueError:
            continue
        row = {"trace": trace, "date": parts[1], "time": parts[2], "lat": lat, "lon": lon}
        if len(parts) > 7:
            try:
                row["elevation"] = float(parts[7])
            except ValueError:
                pass
        rows.append(row)
    return rows


__all__ = ["read_impulseradar", "read_mala", "read_cor"]
