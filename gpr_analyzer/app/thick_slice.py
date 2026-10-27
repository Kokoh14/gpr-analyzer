"""Depth-coloured top view of measured returns. Display only; no spatial infill."""
from __future__ import annotations

import base64
import io
import math

import numpy as np
from PIL import Image

from gpr_analyzer.app.scene3d import local_en, read_layout
from gpr_analyzer.core import depth_from_time

CELL_M = .2
MAX_CELLS = 2_000_000


def depth_rgb(fraction: np.ndarray) -> np.ndarray:
    """Yellow at top, green halfway, blue at bottom."""
    f = np.clip(fraction, 0, 1)
    return np.stack((np.clip(1 - 2 * f, 0, 1), np.clip(2 - 2 * f, 0, 1),
                     np.clip(2 * f - 1, 0, 1)), axis=-1)


def strongest_returns(envelope, dt_ns, velocity, separation, top_m, bottom_m):
    """One maximum and its actual sample depth per trace, including nonlinear depth geometry."""
    env = np.asarray(envelope)
    depths = depth_from_time(np.arange(env.shape[0]) * dt_ns, velocity, separation)
    rows = np.flatnonzero((depths >= top_m) & (depths <= bottom_m))
    if not len(rows):
        return None
    data = np.nan_to_num(np.abs(env[rows]), nan=0., posinf=0., neginf=0.)
    picks = data.argmax(axis=0)
    return data[picks, np.arange(env.shape[1])], depths[rows[picks]]


def _gps(ln):
    fixes = {}
    for g in ln.raw.metadata.get("gps") or []:
        try:
            tr, lat, lon = (float(g[k]) for k in ("trace", "lat", "lon"))
            if not all(math.isfinite(v) for v in (tr, lat, lon)) or tr != int(tr):
                continue
            if abs(lat) > 90 or abs(lon) > 180 or lat == lon == 0 or g.get("quality") == 0:
                continue
            fixes.setdefault(int(tr), (lat, lon))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(fixes.items())


def render(job, top_m: float, bottom_m: float, contrast_db: float = 40., frame: str = "gps") -> dict:
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (top_m, bottom_m, contrast_db)):
        raise ValueError("depth and contrast must be finite numbers")
    if not 0 <= top_m < bottom_m or not 1 <= contrast_db <= 100 or frame not in ("gps", "local"):
        raise ValueError("need 0 <= top < bottom, contrast 1–100 dB, frame gps/local")
    layout = read_layout(job)["lines"]
    fixes = {ln.line_id: _gps(ln) for ln in job.lines}
    first = next((f[0][1] for f in fixes.values() if len(f) >= 2), None)
    batches, excluded = [], []
    for ln in job.lines:
        a = ln.result.analysis
        if not ln.options.time_zero and not a.metadata.get("zero_level_sample"):
            excluded.append({"line_id": ln.line_id, "reason": "surface time zero unknown"})
            continue
        # When time zero was disabled, retain the header surface reference.
        env = ln.result.envelope.data
        zero = int(a.metadata.get("zero_level_sample") or 0)
        strongest = strongest_returns(env[zero:], a.dt_ns, ln.result.velocity_m_per_ns,
                                      a.metadata.get("antenna_separation_m") or 0., top_m, bottom_m)
        if strongest is None:
            excluded.append({"line_id": ln.line_id, "reason": "no samples in depth range"})
            continue
        amp, depth = strongest
        tr = np.arange(a.n_traces)
        valid = np.zeros(a.n_traces, dtype=bool)
        if frame == "local" and ln.line_id in layout:
            p = layout[ln.line_id]
            xy = np.asarray(p["start"]) + np.linspace(0, 1, a.n_traces)[:, None] * (np.asarray(p["end"]) - p["start"])
            valid[:] = True
        elif frame == "gps" and first and len(fixes[ln.line_id]) >= 2:
            f = fixes[ln.line_id]
            ft = np.array([g[0] for g in f])
            ll = np.array([g[1] for g in f])
            xy = local_en(np.interp(tr, ft, ll[:, 0]), np.interp(tr, ft, ll[:, 1]), *first)
            for start, end in zip(ft, ft[1:]):
                # Do not draw unsupported long navigation gaps or extrapolated endpoints.
                if a.dx_m and (end - start) * a.dx_m <= 5:
                    valid |= (tr >= start) & (tr <= end)
            valid |= np.isin(tr, ft)
        else:
            excluded.append({"line_id": ln.line_id, "reason": "no positioning in selected frame"})
            continue
        for section in ln.sections:
            valid &= ~((tr >= section["trace_start"]) & (tr <= section["trace_end"]))
        if valid.any():
            batches.append((xy[valid], amp[valid], depth[valid]))
    if not batches:
        raise ValueError("no positioned samples in this depth range/frame; supply GPS or local layout")
    xy = np.concatenate([b[0] for b in batches])
    amplitude = np.concatenate([b[1] for b in batches])
    depth = np.concatenate([b[2] for b in batches])
    lower = np.floor(xy.min(axis=0) / CELL_M) * CELL_M
    ij = np.floor((xy - lower) / CELL_M + 1e-9).astype(int)
    width, height = (ij.max(axis=0) + 1).tolist()
    if width * height > MAX_CELLS or max(width, height) > 4096:
        raise ValueError("survey exceeds 0.2 m display grid limit; choose a smaller survey")
    # Keep the strongest sample per map cell, including at crossing lines.
    flat = ij[:, 1] * width + ij[:, 0]
    order = np.argsort(-amplitude, kind="stable")
    _, k = np.unique(flat[order], return_index=True)
    chosen = order[k]
    ref = float(amplitude.max()) or 1.
    intensity = np.clip(1 + 20 * np.log10(np.maximum(amplitude[chosen] / ref, 1e-20)) / contrast_db, 0, 1)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    colors = depth_rgb((depth[chosen] - top_m) / (bottom_m - top_m))
    x, y = ij[chosen].T
    rgba[height - 1 - y, x, :3] = np.round(colors * intensity[:, None] * 255).astype(np.uint8)
    rgba[height - 1 - y, x, 3] = np.where(amplitude[chosen] > 0, 255, 0)
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, format="PNG")
    return {"png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "top_m": top_m, "bottom_m": bottom_m, "contrast_db": contrast_db,
            "frame": frame, "cell_m": CELL_M, "width": width, "height": height,
            "bounds_m": [*lower.tolist(), *(lower + [width * CELL_M, height * CELL_M]).tolist()],
            "origin_latlon": list(first) if frame == "gps" else None,
            "cells": len(chosen), "excluded": excluded,
            "display_only": True, "position_source": "GPS interpolation or user local layout; no spatial infill"}
