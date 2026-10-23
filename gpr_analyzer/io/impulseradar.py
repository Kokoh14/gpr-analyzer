"""ImpulseRadar (.iprh / .iprb) and MALA (.rad / .rd3 / .rd7) readers.

Both vendors use the same layout (public spec "CrossOver Data Files Formats" v1.1 and MALA docs):
- header: text file, one 'KEY: value' per line
- data: raw little-endian integers, no trace headers, traces stored one after another
- trace count is taken from the file size, not from LAST TRACE (which can be stale)
ImpulseRadar dual-channel units write two pairs of files, suffix _0 (high frequency) and _1 (low).

Checked against real CrossOver 4080 files (ViewPoint 1.4.18, 400/800 MHz, DATA VERSION 32):
- DISTANCE INTERVAL is the measured wheel step (STOP POSITION / traces); USER DISTANCE INTERVAL is
  only the requested setting and can be 7 % off, so it is used only as a fallback.
- the .cor (GPS), .time and .mrk side files of a dual-channel line are named without the _0/_1
  suffix for .cor/.time, and with it for .mrk.
- ZERO LEVEL is the sample ViewPoint treats as depth 0 (see processing/viewpoint.py).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram, trace_bounds, crop_metadata
from gpr_analyzer.io._util import parse_key_value_header, read_text, sibling, to_float

_CHANNEL_NAMES = {"0": "high frequency", "1": "low frequency"}


def _line_stem(data_path: Path) -> str:
    """'LINE_0001_0' -> 'LINE_0001' for dual-channel files, else the stem unchanged."""
    stem = data_path.stem
    if len(stem) > 2 and stem[-2] == "_" and stem[-1] in _CHANNEL_NAMES:
        return stem[:-2]
    return stem


def _side_file(data_path: Path, suffix: str) -> Path | None:
    """Side file for this channel (stem + suffix), else the one shared by both channels."""
    for stem in (data_path.stem, _line_stem(data_path)):
        p = data_path.with_name(stem + suffix)
        if p.exists():
            return p
    return None


def _read_pair(header_path: Path, data_path: Path, bytes_per_sample: int, fmt: str,
               range_m: tuple[float | None, float | None] | None = None) -> Radargram:
    header = parse_key_value_header(read_text(header_path))
    n_samples = int(to_float(header.get("SAMPLES")) or 0)
    if n_samples <= 0:
        raise ValueError(f"{header_path}: SAMPLES missing or zero")

    n_values = data_path.stat().st_size // bytes_per_sample
    n_traces, rest = divmod(n_values, n_samples)
    if n_traces == 0:
        raise ValueError(f"{data_path}: no complete trace in the file ({n_values} samples, {n_samples} per trace)")

    # FREQUENCY is the sampling frequency in MHz.
    fs_mhz = to_float(header.get("FREQUENCY"))
    window_ns = to_float(header.get("TIMEWINDOW"))
    if fs_mhz:
        dt_ns = 1000.0 / fs_mhz
    elif window_ns:
        dt_ns = window_ns / n_samples
    else:
        raise ValueError(f"{header_path}: neither FREQUENCY nor TIMEWINDOW given")

    # Measured step first: on the real CrossOver files DISTANCE INTERVAL * traces == STOP POSITION,
    # while USER DISTANCE INTERVAL (the requested 0.02 m) is 7 % longer than what was recorded.
    dx = None
    for key in ("DISTANCE INTERVAL", "USER DISTANCE INTERVAL"):
        v = to_float(header.get(key))
        if v is not None and v > 0:
            dx = v
            break

    lo, hi = trace_bounds(n_traces, dx, *range_m) if range_m is not None else (0, n_traces - 1)
    raw = np.fromfile(data_path, dtype=f"<i{bytes_per_sample}", count=(hi - lo + 1) * n_samples,
                       offset=lo * n_samples * bytes_per_sample)
    data = raw.reshape(hi - lo + 1, n_samples).T.astype(np.float32)
    meta = {
        "format": fmt,
        "source": str(data_path),
        "header": header,
        "antenna": header.get("ANTENNA") or header.get("ANTENNAS"),
        "n_traces_from_header": to_float(header.get("LAST TRACE")),
        "partial_trace_dropped": bool(rest),
        # all-zero traces (CrossOver lines usually start with one)
        "dead_traces": [int(i) + lo for i in np.where(np.ptp(data, axis=0) == 0)[0]],
    }
    f_nominal = to_float(header.get("ANTENNA") or header.get("ANTENNAS"))
    if f_nominal and f_nominal > 0:
        meta["antenna_frequency_mhz"] = f_nominal
    sep = to_float(header.get("ANTENNA SEPARATION"))
    if sep and sep > 0:
        meta["antenna_separation_m"] = sep
    zl = to_float(header.get("ZERO LEVEL"))
    if zl and 0 < zl < n_samples:
        meta["zero_level_sample"] = int(zl)
    stem = data_path.stem
    if fmt == "impulseradar" and len(stem) > 2 and stem[-2] == "_" and stem[-1] in _CHANNEL_NAMES:
        meta["channel"] = int(stem[-1])
        meta["channel_name"] = _CHANNEL_NAMES[stem[-1]]
    cor = _side_file(data_path, ".cor")
    if cor is not None:
        meta["gps"] = read_cor(cor)
    mrk = _side_file(data_path, ".mrk")
    if mrk is not None:
        marks = read_mrk(mrk)
        if marks:
            meta["markers"] = marks
    if range_m is not None:
        meta = crop_metadata(meta, lo, hi, n_traces, dx)
    # SOIL VELOCITY is the field velocity in m/us (CrossOver spec); 0 or missing = not set
    v_soil = to_float(header.get("SOIL VELOCITY"))
    if v_soil and 10.0 <= v_soil <= 300.0:
        meta["velocity_m_per_ns"] = v_soil / 1000.0
        return Radargram(data, dt_ns=dt_ns, dx_m=dx, velocity_m_per_ns=v_soil / 1000.0, metadata=meta)
    return Radargram(data, dt_ns=dt_ns, dx_m=dx, metadata=meta)


def read_impulseradar(path: str | Path, *, range_m: tuple[float | None, float | None] | None = None) -> list[Radargram]:
    """Read one ImpulseRadar profile from its .iprh or .iprb file."""
    path = Path(path)
    header_path = path if path.suffix.lower() == ".iprh" else sibling(path, ".iprh")
    data_path = path if path.suffix.lower() == ".iprb" else sibling(path, ".iprb")
    header = parse_key_value_header(read_text(header_path))
    version = int(to_float(header.get("DATA VERSION")) or 16)
    if version not in (16, 32):
        raise ValueError(f"{header_path}: unsupported DATA VERSION {version}")
    return [_read_pair(header_path, data_path, version // 8, "impulseradar", range_m)]


def read_mala(path: str | Path, *, range_m: tuple[float | None, float | None] | None = None) -> list[Radargram]:
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
    return [_read_pair(header_path, data_path, nbytes, "mala", range_m)]


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


def read_mrk(path: str | Path) -> list[dict]:
    """Read a ViewPoint .mrk marker file (header line 'HEADER VERSION:300', then one marker per line).

    Tab-separated: trace, sample (in this channel's samples), type, colour, status, (empty),
    lat, N/S, lon, E/W, elevation, 'M', quality, ... , id. Position fields are optional.
    Files whose first line is not a marker (the header) are handled; junk lines are skipped.
    """
    rows = []
    for line in read_text(Path(path)).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            row = {"trace": int(parts[0]), "sample": int(parts[1])}
        except ValueError:
            continue
        if len(parts) > 2 and parts[2]:
            row["type"] = parts[2]
        if len(parts) > 3 and parts[3]:
            row["colour"] = parts[3]
        if len(parts) > 4 and parts[4].strip():
            row["status"] = parts[4].strip()
        if len(parts) > 9:
            try:
                row["lat"] = float(parts[6]) * (-1 if parts[7].upper() == "S" else 1)
                row["lon"] = float(parts[8]) * (-1 if parts[9].upper() == "W" else 1)
            except ValueError:
                pass
        if len(parts) > 1 and len(parts[-1]) >= 32 and parts[-1].count("-") == 4:
            row["id"] = parts[-1]
        rows.append(row)
    return rows


__all__ = ["read_impulseradar", "read_mala", "read_cor", "read_mrk"]
