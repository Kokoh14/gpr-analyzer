"""3D mode 2: a gridded envelope volume from positioned 2D lines, every cell labelled by where its value came from.

Condor builds its volume from dense Raptor array swaths and fills gaps up to a "max gap" by bilinear
interpolation per time slice. Our input is single-channel 2D lines, often metres apart, so the rules are
stricter and each one is reported with the result:

* Position: GPS fixes interpolated by trace index (mode 1 rules: no extrapolation past the first/last fix),
  and no bridging of fix gaps longer than MAX_FIX_GAP_M of travel; or the user's straight local layout.
  Reviewer-excluded sections are left out.
* Depth: each line's own time zero, velocity and antenna separation (core.depth_from_time). The envelope is
  resampled linearly onto one common depth axis.
* Amplitude: Hilbert envelope divided by that line's 99.5th percentile, in dB down to FLOOR_DB. Lines are
  gained separately, so this is relative energy, not calibrated reflectivity and not a probability.
* Measured cell: at least one trace lies inside it (mean per line, then mean over lines).
* Interpolated cell: no trace inside, but it lies between two different lines: the directions to their
  nearest measured cells are more than 90 degrees apart and the two distances add up to at most max_gap
  (Condor's "max gap"). Value: inverse-distance-squared mean of the measured cells within max_gap.
  auto_gap sets max_gap from the measured line spacing (line_spacing), for dense array swaths whose
  spacing the user does not type in.
* Everything else is empty: nothing is extrapolated beyond the outer lines or past line ends.
"""
from __future__ import annotations

import base64
import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from gpr_analyzer.app.scene3d import _fixes, local_en, read_layout
from gpr_analyzer.core import depth_from_time

FLOOR_DB = -40.0
REF_PERCENTILE = 99.5
MAX_FIX_GAP_M = 5.0
MAX_CELLS = 400_000
MAX_VOXELS = 8_000_000
MEASURED, INTERPOLATED, EMPTY = 2, 1, 0


def _check(name: str, value: Any, lo: float, hi: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a number from {lo} to {hi}")
    return float(value)


def _origin(job: Any, frame: str) -> dict | None:
    """Same origin as mode 1 (first line with two fixes), so both modes share coordinates."""
    if frame != "gps":
        return None
    first = next((f[0] for f in (_fixes(ln) for ln in job.lines) if len(f) >= 2), None)
    return {"lat": first["lat"], "lon": first["lon"]} if first else None


def positions(ln: Any, frame: str, origin: dict | None, layout: dict) -> tuple[np.ndarray | None, str | None]:
    """East/north (m) per processed trace, NaN where the trace has no supported position."""
    r = ln.result.analysis
    n = r.n_traces
    tr = np.arange(n)
    if frame == "local":
        p = layout["lines"].get(ln.line_id)
        if not p:
            return None, "local start/end coordinates not supplied"
        a, b = np.asarray(p["start"], float), np.asarray(p["end"], float)
        xy = a + tr[:, None] / max(n - 1, 1) * (b - a)
    else:
        f = _fixes(ln)
        if not origin or len(f) < 2:
            return None, "no usable GPS track; provide local start/end coordinates"
        ft = np.array([v["trace"] for v in f], float)
        lat = np.interp(tr, ft, [v["lat"] for v in f])
        lon = (np.degrees(np.interp(tr, ft, np.unwrap(np.radians([v["lon"] for v in f])))) + 180) % 360 - 180
        xy = local_en(lat, lon, origin["lat"], origin["lon"])
        ok = (tr >= ft[0]) & (tr <= ft[-1])
        if r.dx_m:
            # A long stretch without fixes is not bridged; the fixes themselves stay.
            for t0, t1 in zip(ft[:-1], ft[1:]):
                if (t1 - t0) * r.dx_m > MAX_FIX_GAP_M:
                    ok &= ~((tr > t0) & (tr < t1))
        xy[~ok] = np.nan
    for s in getattr(ln, "sections", None) or []:
        xy[(tr >= s["trace_start"]) & (tr <= s["trace_end"])] = np.nan
    if not np.isfinite(xy).all(axis=1).any():
        return None, "no positioned traces left (GPS coverage or excluded sections)"
    return xy, None


def _depths(ln: Any) -> tuple[np.ndarray, np.ndarray] | None:
    """Row indices at or below time zero and their depths (m) for this line."""
    r = ln.result.analysis
    if not getattr(getattr(ln, "options", None), "time_zero", True) and not r.metadata.get("zero_level_sample"):
        return None
    zero = float(r.metadata.get("zero_level_sample") or 0) * r.dt_ns
    times = r.time_axis() - zero
    rows = np.flatnonzero(times >= 0)
    if len(rows) < 2:
        return None
    return rows, depth_from_time(times[rows], r.velocity_m_per_ns, r.metadata.get("antenna_separation_m") or 0)


def _resample(env: np.ndarray, rows: np.ndarray, depths: np.ndarray, z: np.ndarray) -> np.ndarray:
    """(len(z), traces) envelope at depths z by linear interpolation between samples; NaN outside the record."""
    frac = np.interp(z, depths, np.arange(len(rows), dtype=float), left=np.nan, right=np.nan)
    ok = np.isfinite(frac)
    out = np.full((len(z), env.shape[1]), np.nan, dtype=np.float32)
    if ok.any():
        f = frac[ok]
        i0 = np.floor(f).astype(int)
        i1 = np.minimum(i0 + 1, len(rows) - 1)
        w = (f - i0)[:, None]
        out[ok] = (1 - w) * env[rows[i0]] + w * env[rows[i1]]
    return out


def _tracks(xy: np.ndarray, max_points: int = 200) -> list[list[list[float]]]:
    """Positioned stretches of the line, each thinned for drawing."""
    good = np.isfinite(xy).all(axis=1)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], good.astype(int), [0]])))
    out = []
    for a, b in zip(edges[::2], edges[1::2]):
        idx = np.unique(np.linspace(a, b - 1, min(b - a, max_points)).round().astype(int))
        if len(idx) >= 2:
            out.append(xy[idx].round(4).tolist())
    return out


def line_spacing(tracks: list[np.ndarray]) -> float | None:
    """Typical distance (m) from a line to its nearest other line: the median over lines of each line's median.

    Points where lines cross pull a line's median down only when they are most of it, so crossing grids
    still give the parallel spacing. None with fewer than two positioned lines.
    """
    pts = [xy[np.isfinite(xy).all(axis=1)] for xy in tracks]
    pts = [p[np.unique(np.linspace(0, len(p) - 1, min(len(p), 200)).round().astype(int))] for p in pts if len(p)]
    if len(pts) < 2:
        return None
    per_line = []
    for k, p in enumerate(pts):
        others = np.concatenate([q for j, q in enumerate(pts) if j != k])
        per_line.append(float(np.median(cKDTree(others).query(p)[0])))
    return float(np.median(per_line))


def _encode(values: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(values, dtype=np.uint8).tobytes()).decode("ascii")


def survey_volume(job: Any, frame: str = "gps", cell_m: float = 0.1, dz_m: float = 0.05,
                  max_gap_m: float = 0.5, max_depth_m: float | None = None, auto_gap: bool = False) -> dict:
    if frame not in ("gps", "local"):
        raise ValueError("frame must be gps or local")
    cell_m = _check("cell_m", cell_m, 0.02, 2.0)
    dz_m = _check("dz_m", dz_m, 0.005, 0.5)
    max_gap_m = _check("max_gap_m", max_gap_m, 0.0, 5.0)
    if max_depth_m is not None:
        max_depth_m = _check("max_depth_m", max_depth_m, dz_m, 100.0)
    layout = read_layout(job)
    origin = _origin(job, frame)
    used, excluded = [], []
    for ln in job.lines:
        xy, reason = positions(ln, frame, origin, layout)
        d = _depths(ln) if not reason else None
        if not reason and d is None:
            reason = "time zero disabled and no header zero; relative surface depth is unknown"
        if reason:
            excluded.append({"line_id": ln.line_id, "source_file": ln.source_file, "reason": reason})
            continue
        used.append((ln, xy, *d))
    if not used:
        raise ValueError("no positioned lines in this frame; supply GPS or a local layout")
    spacing = line_spacing([xy for _, xy, *_ in used])
    if auto_gap:
        # 10 % over the spacing so lines that wander a little still pair up; 0 (no filling) without two lines
        max_gap_m = round(min(spacing * 1.1, 5.0), 2) if spacing else 0.0

    bottom = max(float(dep[-1]) for *_, dep in used)
    if max_depth_m is not None:
        bottom = min(bottom, max_depth_m)
    z = np.arange(0.0, bottom + dz_m / 2, dz_m)
    pts = np.concatenate([xy[np.isfinite(xy).all(axis=1)] for _, xy, *_ in used])
    lo = np.floor(pts.min(axis=0) / cell_m) * cell_m
    nx, ny = (np.floor((pts.max(axis=0) - lo) / cell_m + 1e-9).astype(int) + 1).tolist()
    if nx * ny > MAX_CELLS or nx * ny * len(z) > MAX_VOXELS:
        raise ValueError(f"grid of {nx}×{ny} cells × {len(z)} depths is too large; use a larger cell or depth step, "
                         "or a smaller maximum depth")

    # One record per (line, cell): the mean relative envelope of that line's traces inside the cell.
    keys, labels, values = [], [], []
    lines_out = []
    for k, (ln, xy, rows, dep) in enumerate(used):
        good = np.flatnonzero(np.isfinite(xy).all(axis=1))
        env = np.abs(np.nan_to_num(ln.result.envelope.data[:, good].astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0))
        ref = float(np.percentile(env[rows], REF_PERCENTILE)) or float(env[rows].max()) or 1.0
        res = _resample(env / ref, rows, dep, z)  # (nz, traces)
        ij = np.floor((xy[good] - lo) / cell_m + 1e-9).astype(int)
        key = ij[:, 1] * nx + ij[:, 0]
        uniq, inv = np.unique(key, return_inverse=True)
        total = np.zeros((len(uniq), len(z)))
        count = np.zeros((len(uniq), len(z)))
        np.add.at(total, inv, np.nan_to_num(res.T, nan=0.0))
        np.add.at(count, inv, np.isfinite(res.T))
        with np.errstate(invalid="ignore", divide="ignore"):
            values.append(np.where(count > 0, total / count, np.nan))
        keys.append(uniq)
        labels.append(np.full(len(uniq), k))
        lines_out.append({"line_id": ln.line_id, "source_file": ln.source_file,
                          "velocity_m_per_ns": ln.result.velocity_m_per_ns,
                          "velocity_source": ln.result.velocity_source_code,
                          "antenna_separation_m": ln.result.analysis.metadata.get("antenna_separation_m") or 0,
                          "max_depth_m": round(float(dep[-1]), 4), "reference_envelope": ref,
                          "positioned_traces": int(len(good)), "n_traces": int(ln.result.analysis.n_traces),
                          "tracks": _tracks(xy)})
    keys, labels, values = np.concatenate(keys), np.concatenate(labels), np.concatenate(values)
    centres = np.column_stack([keys % nx, keys // nx]) * cell_m + lo + cell_m / 2

    volume = np.full((len(z), ny * nx), np.nan)
    kind = np.zeros(ny * nx, dtype=np.uint8)
    uniq, inv = np.unique(keys, return_inverse=True)
    total = np.zeros((len(uniq), len(z)))
    count = np.zeros((len(uniq), len(z)))
    np.add.at(total, inv, np.nan_to_num(values, nan=0.0))
    np.add.at(count, inv, np.isfinite(values))
    with np.errstate(invalid="ignore", divide="ignore"):
        volume[:, uniq] = np.where(count > 0, total / count, np.nan).T
    kind[uniq] = MEASURED

    if max_gap_m > cell_m and len(set(labels.tolist())) >= 2:
        tree = cKDTree(centres)
        empty = np.flatnonzero(kind == EMPTY)
        cx = np.column_stack([empty % nx, empty // nx]) * cell_m + lo + cell_m / 2
        for cell, c, near in zip(empty, cx, tree.query_ball_point(cx, max_gap_m)):
            near = np.asarray(near, dtype=int)
            lab = labels[near] if len(near) else near
            if len(near) < 2 or (lab == lab[0]).all():
                continue
            offset = centres[near] - c
            dist = np.hypot(offset[:, 0], offset[:, 1])
            # Nearest measured cell of each line. The cell must lie between two lines (directions more than
            # 90 degrees apart) whose nearest cells are at most max_gap apart along the way through it.
            nearest = np.array([np.flatnonzero(lab == k)[np.argmin(dist[lab == k])] for k in np.unique(lab)])
            u = offset[nearest] / np.maximum(dist[nearest], 1e-12)[:, None]
            span = dist[nearest][:, None] + dist[nearest][None, :]
            if not ((u @ u.T < -1e-9) & (span <= max_gap_m + 1e-9)).any():
                continue
            w = 1.0 / np.maximum(dist, cell_m / 2) ** 2
            v = values[near]
            ok = np.isfinite(v)
            with np.errstate(invalid="ignore", divide="ignore"):
                volume[:, cell] = (np.nan_to_num(v) * w[:, None]).sum(axis=0) / (ok * w[:, None]).sum(axis=0)
            kind[cell] = INTERPOLATED

    db = 20 * np.log10(np.maximum(volume, 10 ** (FLOOR_DB / 20)))
    code = np.where(np.isfinite(volume), 1 + np.round((np.clip(db, FLOOR_DB, 0) - FLOOR_DB) / -FLOOR_DB * 254), 0)
    n_measured, n_interp = int((kind == MEASURED).sum()), int((kind == INTERPOLATED).sum())
    return {
        "schema": "gpr-analyzer/volume3d/1", "job_id": job.job_id, "title": job.info.project, "frame": frame,
        "origin_wgs84": origin, "surface": "relative level surface z=0 m; no terrain correction",
        "grid": {"x0_m": float(lo[0]), "y0_m": float(lo[1]), "cell_m": cell_m, "nx": nx, "ny": ny,
                 "dz_m": dz_m, "nz": len(z), "depth_m": z.round(6).tolist()},
        "parameters": {"max_gap_m": max_gap_m, "max_gap_source": "line spacing" if auto_gap else "user",
                       "line_spacing_m": round(spacing, 3) if spacing is not None else None, "floor_db": FLOOR_DB,
                       "reference": f"per-line {REF_PERCENTILE} percentile of envelope = 0 dB",
                       "max_fix_gap_m": MAX_FIX_GAP_M},
        # volume: uint8 per (depth, north row, east column); 0 = no data, 1..255 = FLOOR_DB..0 dB.
        "volume_b64": _encode(code), "cells_b64": _encode(kind),
        "counts": {"measured": n_measured, "interpolated": n_interp, "empty": nx * ny - n_measured - n_interp},
        "lines": lines_out, "excluded": excluded, "layout": layout,
        "zones": survey_zones(job, frame, origin, layout)["zones"],
        "limitations": [
            "Relative energy per line (separately gained); not calibrated reflectivity, not a probability",
            "Interpolated cells lie between two lines at most max_gap apart; they are estimates, not measurements",
            "Single velocity per line for depth; no 3D migration; no terrain elevation",
            "GPS between fixes is interpolated by trace; gaps longer than 5 m of travel are left empty"],
    }


def survey_zones(job: Any, frame: str = "gps", origin: dict | None = None, layout: dict | None = None) -> dict:
    """Detected and reviewer-drawn zones placed in the same east/north/depth frame as the volume."""
    layout = layout or read_layout(job)
    if frame == "gps" and origin is None:
        origin = _origin(job, frame)
    zones, unplaced = [], []
    for ln in job.lines:
        xy, reason = positions(ln, frame, origin, layout) if ln.zones else (None, None)
        excluded = getattr(ln, "excluded", None)
        for z in ln.zones:
            base = {"line_id": ln.line_id, "zone_id": z.zone_id}
            if excluded and excluded(z):
                continue
            if reason:
                unplaced.append({**base, "reason": reason})
                continue
            idx = np.arange(max(z.trace_start, 0), min(z.trace_end, len(xy) - 1) + 1)
            idx = idx[np.isfinite(xy[idx]).all(axis=1)] if len(idx) else idx
            if not len(idx):
                unplaced.append({**base, "reason": "zone traces have no position"})
                continue
            pick = idx[np.unique(np.linspace(0, len(idx) - 1, min(len(idx), 24)).round().astype(int))]
            zones.append({**base, "trace_start": z.trace_start, "trace_end": z.trace_end,
                          "positioned_traces": int(len(idx)), "n_traces": z.trace_end - z.trace_start + 1,
                          "path_m": xy[pick].round(4).tolist(),
                          "depth_top_m": round(z.depth_top_m, 4), "depth_bottom_m": round(z.depth_bottom_m, 4),
                          "velocity_m_per_ns": z.velocity_m_per_ns, "suspected_type": z.suspected_type,
                          "confidence": z.confidence, "review": z.review})
    return {"frame": frame, "origin_wgs84": origin, "zones": zones, "unplaced": unplaced}
