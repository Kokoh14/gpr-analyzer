"""Positions on the map for the review screen: GPS per trace and a top-view energy slice.

ImpulseRadar writes GPS fixes to the line's .cor file (read by io.impulseradar.read_cor into
metadata["gps"]), one row every few traces. Every trace gets a position by interpolating along the
trace index. The top-view slice is shown like the depth slices of IDS AiMaps on a single line: for each
depth band, the strongest Hilbert-envelope value per trace in dB below the line's maximum. It is a
display aid (relative energy), not a probability and not a score.

The dielectric map uses the diffraction hyperbolas fitted during processing: each fit gives the average
wave velocity from the ground surface down to its apex, so εr = (c / v)². Values exist only at the fitted
points; nothing is interpolated between them.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from gpr_analyzer.core import depth_from_time

EARTH_RADIUS_M = 6371008.8
SLICE_FLOOR_DB = -40
LIGHT_M_PER_NS = 0.299792458
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
                  max_traces: int = MAX_MAP_TRACES, separation_m: float = 0.0) -> dict[str, Any]:
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
    depth = np.round(depth_from_time(edges * dt_ns, velocity_m_per_ns, separation_m), 3).tolist()
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
    sl = energy_slices(res.envelope.data, a.dt_ns, res.velocity_m_per_ns,
                       separation_m=a.metadata.get("antenna_separation_m") or 0.0)
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


def fix_quality(gps: list[dict[str, Any]] | None, traces: list[int]) -> list[str]:
    """GPS fix quality (NMEA code as written in the .cor, e.g. "4" = RTK fixed) of the last fix at or before each trace."""
    rows = sorted((r for r in gps or [] if isinstance(r.get("trace"), int)), key=lambda r: r["trace"])
    if not rows:
        return ["" for _ in traces]
    tr = np.array([r["trace"] for r in rows])
    return [str(rows[max(int(np.searchsorted(tr, t, side="right")) - 1, 0)].get("quality", "")) for t in traces]


def gps_qa(gps: list[dict[str, Any]] | None, n_traces: int, dx_m: float | None) -> dict[str, Any]:
    """Checks that tell whether a line's place on the map can be trusted, from the raw fixes only.

    weak_fix: share of fixes that are not RTK fixed (quality 4). length_ratio: GPS path / wheel distance;
    jitter in weak fixes makes the GPS path longer. start_gap_m / end_gap_m: distance along the wheel with no
    fix at the start or end of the line (those traces are held at the first or last fix). max_jump: largest
    move between two fixes per trace, in wheel trace spacings.
    """
    rows = sorted((r for r in gps or [] if r.get("lat") is not None and r.get("lon") is not None
                   and (r["lat"], r["lon"]) != (0.0, 0.0)), key=lambda r: r["trace"])
    out: dict[str, Any] = {"fixes": len(rows), "flags": []}
    if len(rows) < 2:
        return out
    q = [str(r.get("quality", "")) for r in rows]
    out["quality_counts"] = {k: q.count(k) for k in sorted(set(q))}
    out["weak_fix_share"] = round(sum(1 for v in q if v and v != "4") / len(q), 2)
    lat, lon = np.array([r["lat"] for r in rows]), np.array([r["lon"] for r in rows])
    out["length_gps_m"] = round(path_length_m(lat, lon), 2)
    out["first_fix_trace"], out["last_fix_trace"] = rows[0]["trace"], rows[-1]["trace"]
    if out["weak_fix_share"] > 0:
        out["flags"].append("weak_fix")
    if dx_m:
        wheel = n_traces * dx_m
        out["length_wheel_m"] = round(wheel, 2)
        out["length_ratio"] = round(out["length_gps_m"] / wheel, 2) if wheel else None
        out["start_gap_m"] = round(max(rows[0]["trace"], 0) * dx_m, 2)
        out["end_gap_m"] = round(max(n_traces - 1 - rows[-1]["trace"], 0) * dx_m, 2)
        steps = [path_length_m(lat[i:i + 2], lon[i:i + 2]) / max(rows[i + 1]["trace"] - rows[i]["trace"], 1)
                 for i in range(len(rows) - 1)]
        out["max_jump"] = round(max(steps) / dx_m, 1)
        if out["length_ratio"] and not 0.9 <= out["length_ratio"] <= 1.1:
            out["flags"].append("length_mismatch")
        if out["start_gap_m"] > 0.3 or out["end_gap_m"] > 0.3:
            out["flags"].append("gap")
        if out["max_jump"] > 5:
            out["flags"].append("jump")
    return out


def job_overview(lines: list[Any]) -> list[dict[str, Any]]:
    """Thin track of every line (about 200 points each) so the map can show the whole survey.

    Lines come in recording order (the time of the first GPS fix) with their number in that order, the GPS
    quality at each point, and the gps_qa checks.
    """
    out = []
    for ln in lines:
        a = ln.raw
        gps = a.metadata.get("gps")
        pos = trace_positions(gps, a.n_traces)
        if pos is None:
            out.append({"line_id": ln.line_id, "source_file": ln.source_file, "has_gps": False})
            continue
        lat, lon = pos
        idx = sorted(set(np.linspace(0, a.n_traces - 1, min(a.n_traces, 200)).astype(int).tolist()))
        first = min((r for r in gps if isinstance(r.get("trace"), int)), key=lambda r: r["trace"])
        out.append({"line_id": ln.line_id, "source_file": ln.source_file, "has_gps": True, "n_traces": a.n_traces,
                    "start_time": f"{first.get('date', '')} {first.get('time', '')}".strip(),
                    "points": [[round(float(lat[i]), 7), round(float(lon[i]), 7)] for i in idx],
                    "traces": idx, "quality": fix_quality(gps, idx), "qa": gps_qa(gps, a.n_traces, a.dx_m)})
    timed = sorted((o for o in out if o["has_gps"]), key=lambda o: (o["start_time"] or "~", o["line_id"]))
    for k, o in enumerate(timed, 1):
        o["order"] = k
    return out


def dielectric(velocity_m_per_ns: float) -> float:
    """Relative permittivity εr = (c / v)² for a low-loss medium."""
    return (LIGHT_M_PER_NS / velocity_m_per_ns) ** 2


def dielectric_points(lines: list[Any]) -> list[dict[str, Any]]:
    """Per line: the velocity used, and every hyperbola fit with its position, depth and εr.

    Fits inside sections the user excluded from the results are left out.
    """
    out = []
    for ln in lines:
        res, a = ln.result, ln.result.analysis
        item: dict[str, Any] = {"line_id": ln.line_id, "has_gps": False,
                                "velocity_m_per_ns": round(res.velocity_m_per_ns, 4),
                                "velocity_source": res.velocity_source_code,
                                "eps_r": round(dielectric(res.velocity_m_per_ns), 1), "points": []}
        out.append(item)
        pos = trace_positions(ln.raw.metadata.get("gps"), a.n_traces)
        if pos is None or not a.dx_m:
            continue
        lat, lon = pos
        item["has_gps"] = True
        for f in res.hyperbola_fits:
            tr = int(round(f.x0_m / a.dx_m))
            if not 0 <= tr < a.n_traces or any(s["trace_start"] <= tr <= s["trace_end"] for s in ln.sections):
                continue
            item["points"].append({"trace": tr, "lat": round(float(lat[tr]), 7), "lon": round(float(lon[tr]), 7),
                                   "depth_m": round(float(depth_from_time(f.t0_ns, f.velocity_m_per_ns,
                                                                         a.metadata.get("antenna_separation_m") or 0.0)), 3),
                                   "velocity_m_per_ns": round(f.velocity_m_per_ns, 4),
                                   "eps_r": round(dielectric(f.velocity_m_per_ns), 1), "r2": round(f.r2, 3)})
    return out
