"""Pre-processing navigation diagnostics; wheel distance remains authoritative."""
from __future__ import annotations

import math

import numpy as np

from gpr_analyzer.app.geo import path_length_m


def positioning_qa(rows: list[dict], n_traces: int, dx_m: float | None) -> dict:
    """Densities use wheel metres, never GPS distance. Gaps are sampling warnings, not outages.

    A 120 degree heading reversal is a sharp-turn heuristic, not a swath collision proof:
    channel spacing and antenna width are not available in these line headers.
    """
    fixes = {}
    for r in rows:
        try:
            tr, lat, lon = (float(r[k]) for k in ("trace", "lat", "lon"))
            if not all(math.isfinite(x) for x in (tr, lat, lon)) or tr != int(tr):
                continue
            if not 0 <= tr <= n_traces or abs(lat) > 90 or abs(lon) > 180 or lat == lon == 0:
                continue
            if r.get("quality") == 0:
                continue
            fixes.setdefault(int(tr), r)
        except (KeyError, TypeError, ValueError):
            continue
    f = [fixes[k] for k in sorted(fixes)]
    length = n_traces * dx_m if dx_m and n_traces else None
    out = {"gps_points_per_m": len(f) / length if length else None,
           "traces_per_m": 1 / dx_m if dx_m else None, "usable_fixes": len(f),
           "start_time": None, "end_time": None, "segments": [], "sharp_turns": [],
           "low_density": False, "max_wheel_gap_m": None,
           "gap_warning_m": 5.0, "sharp_turn_degrees": 120}
    if not f:
        return out
    for key, r in (("start_time", f[0]), ("end_time", f[-1])):
        out[key] = " ".join(str(r.get(k) or "") for k in ("date", "time")).strip() or None
    lat = np.array([r["lat"] for r in f])
    lon = np.array([r["lon"] for r in f])
    gaps = []
    for a, b in zip(f, f[1:]):
        wheel = (b["trace"] - a["trace"]) * dx_m if dx_m else None
        gaps.append(wheel)
        out["segments"].append({"points": [[a["lat"], a["lon"]], [b["lat"], b["lon"]]],
                                "gps_points_per_m": 1 / wheel if wheel else None,
                                "traces_per_m": out["traces_per_m"],
                                "gap": bool(wheel and wheel > 5.0)})
    if dx_m and gaps:
        out["max_wheel_gap_m"] = max(gaps)
        out["low_density"] = max(gaps) > 5.0
    # Local tangent plane is adequate for a per-line turn angle.
    xy = np.column_stack((lon * math.cos(math.radians(float(lat.mean()))), lat))
    delta = np.diff(xy, axis=0)
    for i in range(len(delta) - 1):
        norm = np.linalg.norm(delta[i]) * np.linalg.norm(delta[i + 1])
        if norm <= 1e-20:
            continue
        angle = math.degrees(math.acos(float(np.clip(np.dot(delta[i], delta[i + 1]) / norm, -1, 1))))
        if angle >= 120:
            out["sharp_turns"].append({"trace": f[i + 1]["trace"], "angle_deg": round(angle, 1),
                                       "point": [f[i + 1]["lat"], f[i + 1]["lon"]]})
    out["length_gps_m"] = path_length_m(lat, lon)
    return out
