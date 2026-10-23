"""Positioned survey curtains. Display geometry only; never fills unsurveyed volume."""
from __future__ import annotations

import base64
import io
import json
import math
from copy import copy
from types import SimpleNamespace
from typing import Any

import numpy as np

from gpr_analyzer.core import depth_from_time

MAX_LINES = 80
MAX_COLUMNS = 192
MAX_ROWS = 160
TRACKS = ("grey", "envelope", "viewpoint")


def snapshot(job: Any) -> Any:
    """Called under the job lock; published results/arrays are replaced, never edited in place."""
    return SimpleNamespace(job_id=job.job_id, folder=job.folder, info=copy(job.info),
                           lines=[copy(ln) for ln in job.lines])


def _fixes(ln: Any) -> list[dict]:
    out = {}
    for g in ln.raw.metadata.get("gps") or []:
        try:
            tr, lat, lon = float(g["trace"]), float(g["lat"]), float(g["lon"])
            if not all(math.isfinite(v) for v in (tr, lat, lon)):
                continue
            # Cropped lines retain the fixes just outside the crop to anchor interpolation.
            outside_uncropped = not ln.raw.metadata.get("crop") and not 0 <= tr < ln.raw.n_traces
            if tr != int(tr) or outside_uncropped or abs(lat) > 90 or abs(lon) > 180:
                continue
            if lat == lon == 0:
                continue
            out.setdefault(int(tr), {"trace": int(tr), "lat": lat, "lon": lon})
        except (KeyError, ValueError, TypeError):
            continue
    return [out[t] for t in sorted(out)]


def local_en(lat, lon, lat0: float, lon0: float) -> np.ndarray:
    """WGS84 ECEF to common local east/north at ellipsoid height zero (not terrain)."""
    def ecef(phi, lam):
        phi, lam = np.radians(phi), np.radians(lam)
        n = 6378137.0 / np.sqrt(1 - 6.69437999014e-3 * np.sin(phi) ** 2)
        return np.array([n * np.cos(phi) * np.cos(lam), n * np.cos(phi) * np.sin(lam),
                         n * (1 - 6.69437999014e-3) * np.sin(phi)])
    delta = ecef(lat, lon) - ecef(lat0, lon0)[:, None]
    p, l = np.radians(lat0), np.radians(lon0)
    return np.array([-np.sin(l) * delta[0] + np.cos(l) * delta[1],
                     -np.sin(p) * np.cos(l) * delta[0] - np.sin(p) * np.sin(l) * delta[1] + np.cos(p) * delta[2]]).T


def read_layout(job: Any) -> dict:
    p = job.folder / "scene3d.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"version": 1, "lines": {}}


def save_layout(job: Any, body: dict) -> dict:
    """Validate the entire supplied local layout before writing. Empty entries stay unpositioned."""
    if not isinstance(body.get("lines"), dict):
        raise ValueError("lines must be an object keyed by line_id")
    known = {ln.line_id for ln in job.lines}
    clean = {}
    for lid, value in body["lines"].items():
        if lid not in known:
            raise ValueError(f"unknown line: {lid}")
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{lid}: provide start/end east,north in metres")
        try:
            a, b = np.asarray(value["start"], dtype=float), np.asarray(value["end"], dtype=float)
            if a.shape != (2,) or b.shape != (2,) or not np.isfinite([a, b]).all():
                raise ValueError()
            if np.max(np.abs([a, b])) > 1e7 or np.linalg.norm(b - a) <= 1e-6:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ValueError(f"{lid}: start/end must be different finite 2D metre coordinates") from None
        clean[lid] = {"start": a.tolist(), "end": b.tolist()}
    data = {"version": 1, "lines": clean}
    p = job.folder / "scene3d.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return data


def _texture(ln: Any, rows: np.ndarray, columns: np.ndarray, track: str) -> str:
    from matplotlib import colormaps
    from PIL import Image

    res = ln.result
    cache = getattr(ln, "_cache", None)
    if cache is None:
        cache = ln._cache = {}
    # A processing result is immutable once published. Keeping its identity also prevents id reuse.
    if cache.get("scene3d_result") is not res:
        cache["scene3d_result"] = res
        cache["scene3d_textures"] = {}
        cache["scene3d_contrast"] = {}
    textures, contrast = cache["scene3d_textures"], cache["scene3d_contrast"]
    key = (track, rows.tobytes(), columns.tobytes())
    if key in textures:
        return textures[key]
    d = res.envelope.data if track == "envelope" else (res.viewpoint.data if track == "viewpoint" else res.analysis.data)
    sampled = np.nan_to_num(d[np.ix_(np.minimum(rows, d.shape[0] - 1), columns)].astype(float),
                            copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    if track not in contrast:
        work = np.abs(d)
        np.nan_to_num(work, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        contrast[track] = max(float(np.max(work) if track == "envelope" else
                                   np.percentile(work, 99 if track == "grey" else 98, overwrite_input=True)), 1e-20)
    ref = contrast[track]
    if track == "envelope":
        values = np.clip((20 * np.log10(np.maximum(np.abs(sampled) / ref, 1e-2)) + 40) / 40, 0, 1)
        rgb = colormaps["turbo"](values, bytes=True)[:, :, :3]
    else:
        grey = np.round(np.clip(sampled / ref * 0.5 + 0.5, 0, 1) * 255).astype(np.uint8)
        rgb = np.repeat(grey[:, :, None], 3, axis=2)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    # Bound frame/coverage variants; contrast references remain reusable for the current result.
    if len(textures) >= 6:
        textures.pop(next(iter(textures)))
    textures[key] = png
    return png


def survey_scene(job: Any, frame: str = "gps", track: str = "grey") -> dict:
    if frame not in ("gps", "local") or track not in TRACKS:
        raise ValueError("frame must be gps/local and track grey/envelope/viewpoint")
    layout = read_layout(job)
    all_fixes = {ln.line_id: _fixes(ln) for ln in job.lines}
    first = next((f[0] for f in all_fixes.values() if len(f) >= 2), None)
    origin = {"lat": first["lat"], "lon": first["lon"]} if frame == "gps" and first else None
    lines, excluded = [], []
    for ln in job.lines:
        r = ln.result.analysis
        reason = None
        f = all_fixes[ln.line_id]
        placement = layout["lines"].get(ln.line_id)
        if len(lines) >= MAX_LINES:
            reason = f"display limit: {MAX_LINES} lines"
        elif r.n_traces < 2 or r.n_samples < 2:
            reason = "at least two traces and two samples required"
        elif not getattr(getattr(ln, "options", None), "time_zero", True) and not r.metadata.get("zero_level_sample"):
            reason = "time zero disabled and no header zero; relative surface depth is unknown"
        elif frame == "gps" and (not origin or len(f) < 2):
            reason = "no usable GPS track; provide local start/end coordinates"
        elif frame == "local" and not placement:
            reason = "local start/end coordinates not supplied"
        if reason:
            excluded.append({"line_id": ln.line_id, "source_file": ln.source_file, "reason": reason})
            continue
        # No fabricated GPS position beyond the navigation coverage.
        lo, hi = (max(0, f[0]["trace"]), min(r.n_traces - 1, f[-1]["trace"])) if frame == "gps" else (0, r.n_traces - 1)
        if hi <= lo:
            excluded.append({"line_id": ln.line_id, "source_file": ln.source_file, "reason": "GPS does not bracket two processed traces"})
            continue
        cols = np.unique(np.linspace(lo, hi, min(hi - lo + 1, MAX_COLUMNS)).round().astype(int))
        if frame == "gps":
            tr = [v["trace"] for v in f]
            lat = np.interp(cols, tr, [v["lat"] for v in f])
            # Unwrap longitudes so a date-line crossing is a short path.
            unwrapped = np.unwrap(np.radians([v["lon"] for v in f]))
            lon = (np.degrees(np.interp(cols, tr, unwrapped)) + 180) % 360 - 180
            xy = local_en(lat, lon, origin["lat"], origin["lon"])
            if np.max(np.linalg.norm(xy, axis=1)) > 5000:
                excluded.append({"line_id": ln.line_id, "source_file": ln.source_file,
                                 "reason": "outside the 5 km local GPS scene; split the survey"})
                continue
            latlon = np.column_stack([lat, lon]).round(8).tolist()
        else:
            a, b = np.array(placement["start"]), np.array(placement["end"])
            xy = a + cols[:, None] / (r.n_traces - 1) * (b - a)
            latlon = None
        zero = float(r.metadata.get("zero_level_sample") or 0) * r.dt_ns
        times = r.time_axis() - zero
        valid = np.flatnonzero(times >= 0)
        if len(valid) < 2:
            excluded.append({"line_id": ln.line_id, "source_file": ln.source_file, "reason": "no samples below time zero"})
            continue
        rows = np.unique(np.linspace(valid[0], valid[-1], min(len(valid), MAX_ROWS)).round().astype(int))
        depths = depth_from_time(times[rows], r.velocity_m_per_ns, r.metadata.get("antenna_separation_m") or 0)
        lines.append({"line_id": ln.line_id, "source_file": ln.source_file,
                      "trace_indices": cols.tolist(), "sample_indices": rows.tolist(),
                      "source_trace_indices": (cols + getattr(ln, "trace_offset", 0)).tolist(),
                      "time_ns": times[rows].round(6).tolist(), "depth_m": depths.round(6).tolist(),
                      "xy_m": xy.round(5).tolist(), "latlon": latlon,
                      "navigation": "GPS interpolated between fixes" if frame == "gps" else "manual straight line",
                      "coverage_traces": [int(lo), int(hi)], "n_traces": r.n_traces,
                      "velocity_m_per_ns": r.velocity_m_per_ns, "velocity_source": ln.result.velocity_source_code,
                      "antenna_separation_m": r.metadata.get("antenna_separation_m") or 0,
                      "texture": _texture(ln, rows, cols, track)})
    return {"schema": "gpr-analyzer/scene3d/1", "job_id": job.job_id, "title": job.info.project,
            "frame": frame, "origin_wgs84": origin, "surface": "relative level surface z=0 m; no terrain correction",
            "track": track, "normalization": "per-line maximum, -40..0 dB" if track == "envelope" else "per-line amplitude contrast",
            "lines": lines, "excluded": excluded, "layout": layout,
            "limitations": ["2D measured curtains, not a reconstructed volume", "GPS between fixes is interpolated by trace",
                            "No GPS extrapolation; no surveyed terrain elevation", "Display samples are reduced; source data unchanged"]}
