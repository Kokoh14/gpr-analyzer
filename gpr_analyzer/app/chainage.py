"""Railway chainage (km) from a reference KML, and the trace windows of chosen sections.

The reference KML is the project's own layer file: LineStrings whose ExtendedData give the km at both
ends (กม_เริ่มต้น / กม_สิ้นสุด, or km_start / km_end). A placemark that also has a sequence number
(ลำดับ / seq) is a section to analyse, such as a risk point; the others form the km centreline.

A GPS fix gets the km of the nearest centreline point (centreline densified every 10 m), when it lies
within max_offset_m of the centreline. A section's window on a line is the run of fixes whose km falls
in the section (plus buffer_km on both sides), widened by pad_traces; overlapping windows on one line
are merged so each trace is processed once.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

KM_START = ("กม_เริ่มต้น", "km_start", "km_from")
KM_END = ("กม_สิ้นสุด", "km_end", "km_to")
SEQ = ("ลำดับ", "seq")
STEP_M = 10.0


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(d: dict[str, str], keys: tuple[str, ...]) -> str | None:
    return next((d[k] for k in keys if d.get(k, "").strip()), None)


def fmt_km(km: float | None) -> str:
    """36.1 → '36+100' (railway style)."""
    if km is None:
        return ""
    m = int(round(km * 1000))
    return f"{m // 1000}+{m % 1000:03d}"


@dataclass
class Reference:
    """Centreline points with their km, for km_at()."""

    x: np.ndarray
    y: np.ndarray
    km: np.ndarray
    lat0: float

    def __post_init__(self) -> None:
        self._tree = cKDTree(np.c_[self.x, self.y])

    def _xy(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        return np.c_[np.asarray(lon) * 111320 * math.cos(math.radians(self.lat0)), np.asarray(lat) * 110540]

    def km_at(self, lat: Any, lon: Any) -> tuple[np.ndarray, np.ndarray]:
        """(km, distance from the centreline in m) for each point."""
        d, i = self._tree.query(self._xy(np.atleast_1d(lat), np.atleast_1d(lon)))
        return self.km[i], d


def read_kml(path: str | Path) -> tuple[Reference | None, list[dict[str, Any]]]:
    """(km centreline, sections) from a KML file; the centreline is None when the file has none."""
    root = ET.parse(Path(path).expanduser()).getroot()
    segs, sections = [], []

    def walk(el: ET.Element, folder: str) -> None:
        for c in el:
            t = _local(c.tag)
            if t in ("Folder", "Document"):
                name = next((x.text or "" for x in c if _local(x.tag) == "name"), "")
                walk(c, name.strip() or folder)
            elif t == "Placemark":
                data = {d.get("name", ""): next((v.text or "" for v in d if _local(v.tag) == "value"), "")
                        for d in c.iter() if _local(d.tag) == "Data"}
                k0, k1 = _first(data, KM_START), _first(data, KM_END)
                coords = [x.text for x in c.iter() if _local(x.tag) == "coordinates"]
                if k0 is None or k1 is None or not coords:
                    continue
                pts = [[float(v) for v in p.split(",")[:2]] for p in coords[0].split()]
                seq = _first(data, SEQ)
                if seq is None:
                    segs.append((float(k0), float(k1), np.array(pts)))
                    continue
                kind = data.get("ประเภทพื้นที่") or data.get("type") or folder
                if any(x["id"] == f"{kind}-{seq}" for x in sections):
                    continue  # the same section again, e.g. its label point
                sections.append({"id": f"{kind}-{seq}", "type": kind, "seq": int(float(seq)),
                                 "name": data.get("ชื่อพื้นที่") or data.get("name") or "",
                                 "km0": min(float(k0), float(k1)), "km1": max(float(k0), float(k1)),
                                 "track": data.get("ประเภททาง") or data.get("track") or ""})

    walk(root, "")
    if not segs:
        return None, sections
    lat0 = float(np.mean(np.concatenate([s[2][:, 1] for s in segs])))
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540
    xs, ys, kms = [], [], []
    for k0, k1, c in segs:
        x, y = c[:, 0] * kx, c[:, 1] * ky
        d = np.r_[0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
        if d[-1] <= 0:
            continue
        s = np.r_[np.arange(0, d[-1], STEP_M), d[-1]]
        xs.append(np.interp(s, d, x))
        ys.append(np.interp(s, d, y))
        kms.append(k0 + (k1 - k0) * s / d[-1])
    return Reference(np.concatenate(xs), np.concatenate(ys), np.concatenate(kms), lat0), sections


def section_windows(ref: Reference, sections: list[dict[str, Any]], fixes: list[dict[str, Any]], n_traces: int,
                    buffer_km: float = 0.1, max_offset_m: float = 60.0, pad_traces: int = 50,
                    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """(merged trace windows of the sections on one line, covered fraction per section id)."""
    fx = sorted((f for f in fixes if f.get("lat") is not None and f.get("lon") is not None), key=lambda f: f["trace"])
    if len(fx) < 2 or n_traces < 2:
        return [], {}
    tr = np.array([f["trace"] for f in fx])
    km, dist = ref.km_at([f["lat"] for f in fx], [f["lon"] for f in fx])
    ok = dist <= max_offset_m
    raw, cover = [], {}
    for s in sections:
        idx = np.flatnonzero(ok & (km >= s["km0"] - buffer_km) & (km <= s["km1"] + buffer_km))
        if not idx.size:
            continue
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 2) + 1):
            k0, k1 = sorted((float(km[run[0]]), float(km[run[-1]])))
            span = max(s["km1"] - s["km0"], 1e-3)
            c = (min(k1, s["km1"]) - max(k0, s["km0"])) / span
            if c <= 0.02:
                continue
            cover[s["id"]] = round(min(cover.get(s["id"], 0) + c, 1.0), 3)
            raw.append([max(0, int(tr[run[0]]) - pad_traces), min(n_traces - 1, int(tr[run[-1]]) + pad_traces), s["id"]])
    out: list[dict[str, Any]] = []
    for t0, t1, sid in sorted(raw):
        if out and t0 <= out[-1]["trace1"]:
            out[-1]["trace1"] = max(out[-1]["trace1"], t1)
            if sid not in out[-1]["sections"]:
                out[-1]["sections"].append(sid)
        else:
            out.append({"trace0": t0, "trace1": t1, "sections": [sid]})
    for w in out:
        m = ok & (tr >= w["trace0"] - 2 * pad_traces) & (tr <= w["trace1"] + 2 * pad_traces)
        w["chainage"] = [[int(t), round(float(k), 4)] for t, k in zip(tr[m], km[m])]
        inside = [k for t, k in w["chainage"] if w["trace0"] <= t <= w["trace1"]] or [k for _, k in w["chainage"]]
        w["km_from"], w["km_to"] = round(min(inside), 3), round(max(inside), 3)
    return out, cover


def km_at_trace(chainage: list[list[float]] | None, trace: float, reach: int = 100) -> float | None:
    """km of a trace (absolute in the file) from a window's [[trace, km], ...].

    Beyond the first / last fix (the end of a line has no fix after it) the end slope is extended for up
    to reach traces; further out it is None.
    """
    if not chainage or len(chainage) < 2:
        return None
    t, k = [c[0] for c in chainage], [c[1] for c in chainage]
    if not t[0] - reach <= trace <= t[-1] + reach:
        return None
    if trace < t[0] or trace > t[-1]:
        i = 0 if trace < t[0] else -2
        return float(k[i] + (k[i + 1] - k[i]) * (trace - t[i]) / max(t[i + 1] - t[i], 1))
    return float(np.interp(trace, t, k))


def match_folder(path: str | Path, kml: str | Path, recursive: bool = False, include: set[str] | None = None,
                 chosen: set[str] | None = None, buffer_km: float = 0.1) -> dict[str, Any]:
    """Sections of the KML with the lines of a data folder that cover them, and each line's windows."""
    from gpr_analyzer.app.files import sort_files
    from gpr_analyzer.app.folders import folder_files, line_fixes

    try:
        ref, sections = read_kml(kml)
    except (OSError, ET.ParseError) as e:
        raise ValueError(f"อ่านไฟล์ KML ไม่ได้: {e}") from None
    if ref is None:
        raise ValueError("ไฟล์ KML ไม่มีแนวเส้นทางที่บอก กม. (ExtendedData กม_เริ่มต้น / กม_สิ้นสุด)")
    if not sections:
        raise ValueError("ไฟล์ KML ไม่มีช่วงที่มีลำดับ (ExtendedData ลำดับ)")
    use = [s for s in sections if chosen is None or s["id"] in chosen]
    groups = sorted(sort_files(folder_files(path, recursive)).groups, key=lambda g: str(g.primary).lower())
    lines, by_sec = [], {s["id"]: [] for s in sections}
    for g in groups:
        if g.family not in ("ids", "impulseradar") or (include is not None and str(g.primary) not in include):
            continue
        try:
            fixes, n, dx = line_fixes(g.primary, g.family)
        except Exception:  # noqa: BLE001 - an unreadable line simply covers nothing; the scan reports it
            continue
        if not dx:
            continue
        wins, cover = section_windows(ref, use, fixes, n, buffer_km)
        for w in wins:
            w["range_m"] = [round((w["trace0"] + 0.5) * dx, 3), round((w["trace1"] - 0.5) * dx, 3)]
            w["length_m"] = round((w["trace1"] - w["trace0"]) * dx, 1)
        for sid, c in cover.items():
            by_sec[sid].append({"line": g.stem, "primary": str(g.primary), "cover": c})
        if wins:
            lines.append({"name": g.stem, "primary": str(g.primary), "dx_m": dx, "windows": wins})
    for s in sections:
        s["lines"] = by_sec[s["id"]]
    return {"kml": str(kml), "sections": sections, "lines": lines,
            "length_m": round(sum(w["length_m"] for ln in lines for w in ln["windows"]), 1)}


def plan_windows(match: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """match_folder() lines as Job.plan_files windows: {primary: [{range_m, label, chainage}]}."""
    out: dict[str, list[dict[str, Any]]] = {}
    for ln in match["lines"]:
        out[ln["primary"]] = [{"range_m": w["range_m"], "chainage": w["chainage"], "sections": w["sections"],
                               "label": f"กม. {fmt_km(w['km_from'])}–{fmt_km(w['km_to'])}"} for w in ln["windows"]]
    return out
