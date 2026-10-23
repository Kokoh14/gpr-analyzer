"""Positions on the map for the review screen: GPS per trace and a top-view energy slice.

ImpulseRadar writes GPS fixes to the line's .cor file (read by io.impulseradar.read_cor into
metadata["gps"]), one row every few traces. Every trace gets a position by interpolating along the
trace index. The top-view slice is shown like the depth slices of IDS AiMaps on a single line: for each
depth band, the strongest Hilbert-envelope value per trace in dB below the line's maximum. It is a
display aid (relative energy), not a probability and not a score.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

EARTH_RADIUS_M = 6371008.8
SLICE_FLOOR_DB = -40
MAX_MAP_TRACES = 1500  # traces sent to the map; longer lines are thinned (max over each group)


def trace_positions(gps: list[dict[str, Any]] | None, n_traces: int) -> tuple[np.ndarray, np.ndarray] | None:
    """(lat, lon) for every trace from the GPS rows, or None when there are fewer than two usable fixes."""
    rows = [r for r in (gps or []) if r.get("lat") is not None and r.get("lon") is not None
            and abs(r["lat"]) <= 90 and abs(r["lon"]) <= 180 and (r["lat"], r["lon"]) != (0.0, 0.0)]
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r["trace"])
    tr = np.array([r["trace"] for r in rows], dtype=float)
    keep = np.concatenate([[True], np.diff(tr) > 0])  # one fix per trace number
    tr = tr[keep]
    if tr.size < 2:
        return None
    lat = np.array([r["lat"] for r in rows])[keep]
    lon = np.array([r["lon"] for r in rows])[keep]
    x = np.arange(n_traces, dtype=float)
    return np.interp(x, tr, lat), np.interp(x, tr, lon)


def path_length_m(lat: np.ndarray, lon: np.ndarray) -> float:
    """Length of the GPS path (haversine)."""
    if lat.size < 2:
        return 0.0
    p1, p2 = np.radians(lat[:-1]), np.radians(lat[1:])
    dp, dl = p2 - p1, np.radians(np.diff(lon))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(np.sum(2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))))


def _groups(n: int, max_n: int) -> list[tuple[int, int]]:
    k = max(1, math.ceil(n / max_n))
    return [(i, min(i + k, n)) for i in range(0, n, k)]


def energy_slices(envelope: np.ndarray, dt_ns: float, velocity_m_per_ns: float, n_bands: int = 40,
                  max_traces: int = MAX_MAP_TRACES) -> dict[str, Any]:
    """Strongest envelope per depth band and trace group, in whole dB from SLICE_FLOOR_DB to 0.

    envelope: (samples, traces) with time zero at row 0.
    """
    env = np.abs(np.asarray(envelope, dtype=np.float64))
    ns, nt = env.shape
    ref = env.max() if env.size and env.max() > 0 else 1.0
    groups = _groups(nt, max_traces)
    edges = np.linspace(0, ns, min(n_bands, ns) + 1).astype(int)
    bands = []
    for a, b in zip(edges[:-1], edges[1:]):
        band = env[a:max(b, a + 1)].max(axis=0)
        col = np.array([band[g0:g1].max() for g0, g1 in groups])
        db = 20.0 * np.log10(np.maximum(col / ref, 10 ** (SLICE_FLOOR_DB / 20.0)))
        bands.append([int(round(v)) for v in db])
    depth = [round(float(e * dt_ns * velocity_m_per_ns / 2.0), 3) for e in edges]
    return {"floor_db": SLICE_FLOOR_DB, "depth_edges_m": depth, "db": bands,
            "trace_groups": [[g0, g1 - 1] for g0, g1 in groups]}


def line_map(ln: Any) -> dict[str, Any]:
    """Everything the map panel needs for one processed line (app.jobs.LineState)."""
    res = ln.result
    a = res.analysis
    meta = ln.raw.metadata
    out: dict[str, Any] = {"line_id": ln.line_id, "has_gps": False, "n_traces": a.n_traces,
                           "gps_fixes": len(meta.get("gps") or [])}
    pos = trace_positions(meta.get("gps"), a.n_traces)
    if pos is None:
        return out
    lat, lon = pos
    sl = energy_slices(res.envelope.data, a.dt_ns, res.velocity_m_per_ns)
    centre = [(g0 + g1) // 2 for g0, g1 in sl["trace_groups"]]
    out.update({
        "has_gps": True,
        "length_gps_m": round(path_length_m(lat, lon), 2),
        "length_wheel_m": round(a.n_traces * a.dx_m, 2) if a.dx_m else None,
        # one point per trace group; the group's first and last trace are in slice["trace_groups"]
        "points": [[round(float(lat[i]), 7), round(float(lon[i]), 7)] for i in centre],
        "ends": [[round(float(lat[i]), 7), round(float(lon[i]), 7)] for i in (0, a.n_traces - 1)],
        "slice": sl,
        "markers": [{"trace": m["trace"], "lat": round(float(lat[min(max(m["trace"], 0), a.n_traces - 1)]), 7),
                     "lon": round(float(lon[min(max(m["trace"], 0), a.n_traces - 1)]), 7), "type": m.get("type", "")}
                    for m in meta.get("markers") or [] if isinstance(m.get("trace"), int)],
    })
    return out


def job_overview(lines: list[Any]) -> list[dict[str, Any]]:
    """Thin track of every line (about 200 points each) so the map can show the whole survey."""
    out = []
    for ln in lines:
        a = ln.raw
        pos = trace_positions(a.metadata.get("gps"), a.n_traces)
        if pos is None:
            out.append({"line_id": ln.line_id, "source_file": ln.source_file, "has_gps": False})
            continue
        lat, lon = pos
        idx = sorted(set(np.linspace(0, a.n_traces - 1, min(a.n_traces, 200)).astype(int).tolist()))
        out.append({"line_id": ln.line_id, "source_file": ln.source_file, "has_gps": True,
                    "points": [[round(float(lat[i]), 7), round(float(lon[i]), 7)] for i in idx]})
    return out
