"""Along-track GPS lag of an IDS line, measured on bridges and culverts of a structures KML.

On the user's railway lines (IDS, June 2022) the GPS position of a trace lies a fixed distance AHEAD of
where the radar was: a bridge from the project's structures KML maps 17 m (one outbound line) or 36 m
(one inbound line) past the surface change the radar shows over the same bridge. The lag differs from
line to line, so it is measured on each zone: every KML point near the GPS track is paired with the
nearest surface_change span, and the median trace difference is the lag, used only when at least
MIN_MATCHES points agree. Shifting each fix back by the lag puts the radar features at their real place.

The zone's channel 1 is processed once more for this (analysis track with a sliding mean, ~0.4 s/km).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from gpr_analyzer.app import geo

MAX_DIST_M = 30.0  # a KML point farther than this from the track is on another line
MAX_LAG_M = 60.0  # a span farther than this from the point is not its surface change
MIN_MATCHES = 3


def read_points(path: str | Path) -> np.ndarray:
    """(lat, lon) of every Point placemark of a KML file, shape (n, 2)."""
    pts = []
    for el in ET.parse(Path(path).expanduser()).getroot().iter():
        if el.tag.rsplit("}", 1)[-1] != "Point":
            continue
        txt = next((c.text or "" for c in el if c.tag.rsplit("}", 1)[-1] == "coordinates"), "")
        v = txt.strip().split(",")
        try:
            pts.append((float(v[1]), float(v[0])))
        except (IndexError, ValueError):
            continue
    return np.array(pts, dtype=float).reshape(-1, 2)


def estimate_lag(lat: np.ndarray, lon: np.ndarray, spans: list[tuple[int, int]], points: np.ndarray,
                 dx_m: float) -> tuple[int, int] | None:
    """(lag in traces, number of points used) or None; lag > 0 = the GPS position is ahead of the radar."""
    if not spans or not len(points) or not len(lat):
        return None
    k = 111320 * math.cos(math.radians(float(np.mean(lat))))
    track = np.c_[np.asarray(lon) * k, np.asarray(lat) * 110540]
    centres = np.array([(a + b) / 2 for a, b in spans])
    near = []
    for la, lo in points:
        d = np.hypot(track[:, 0] - lo * k, track[:, 1] - la * 110540)
        j = int(d.argmin())
        if d[j] <= MAX_DIST_M and 0 < j < len(lat) - 1:
            near.append(j)
    lags, last = [], None
    for j in sorted(near):
        if last is not None and j - last <= MAX_DIST_M / dx_m:
            continue  # one structure drawn as several points (twin tracks, both abutments)
        last = j
        off = j - centres[np.abs(centres - j).argmin()]
        if abs(off) * dx_m <= MAX_LAG_M:
            lags.append(off)
    if len(lags) < MIN_MATCHES:
        return None
    return round(float(np.median(lags))), len(lags)


def shift(rows: list[dict[str, Any]], lag: int) -> list[dict[str, Any]]:
    """GPS rows with each fix moved back by lag traces."""
    return [{**r, "trace": r["trace"] - lag} for r in rows]


def zone_lag(primary: Path, points: np.ndarray) -> tuple[int, int, list[dict[str, Any]]] | None:
    """(lag, points used, the zone's GPS rows shifted by the lag) for the IDS zone holding primary, from channel 1."""
    from gpr_analyzer.detect.artifacts import detect_surface_changes
    from gpr_analyzer.io.ids import read_ids
    from gpr_analyzer.processing import ProcessingOptions, run_pipeline

    rg = read_ids(primary, channels={1})[0]
    pos = geo.trace_positions(rg.metadata.get("gps"), rg.n_traces)
    if pos is None or not rg.dx_m:
        return None
    res = run_pipeline(rg, ProcessingOptions(background_statistic="mean"), tracks={"analysis"})
    spans = [(a.trace_start, a.trace_end) for a in detect_surface_changes(res.analysis)]
    r = estimate_lag(pos[0], pos[1], spans, points, rg.dx_m)
    return (*r, shift(rg.metadata["gps"], r[0])) if r else None
