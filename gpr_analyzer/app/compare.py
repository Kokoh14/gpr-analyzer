"""Compare this program's zones with a licensed program's result on the same line.

The reference programs (ImpulseRadar ViewPoint and GeoLitix) are treated as black boxes: we only read what the
user exports or types in from them, never their files or code. For each line the user can keep

- reference images (screenshots or exported radargram pictures), shown beside our views;
- reference picks: boxes or points the reference program marked, imported from a CSV export,
  typed in, or drawn on our radargram where the reference program showed them;
- reference line facts (trace count, time window, length, velocity) to check our file readers;
- a verdict per pick or per zone of ours, which overrides the automatic matching.

Matching is by overlap in distance and depth with a tolerance, one reference pick to at most one
zone. Everything is plain dicts so it saves as JSON next to the job.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any

SOURCES = {"viewpoint": "ViewPoint", "geolitix": "GeoLitix"}
# Older saved jobs used these names; they are read as ViewPoint (the software of the user's ImpulseRadar unit).
LEGACY_SOURCES = {"subscan": "viewpoint", "other": "viewpoint"}
REF_VERDICTS = ("match", "partial", "missed", "unsure")  # for a reference pick
ZONE_VERDICTS = ("extra_real", "extra_false", "unsure")  # for a zone only we found
META_FIELDS = ("n_traces", "time_window_ns", "length_m", "velocity_m_per_ns", "max_depth_m")
FEATURE_RATINGS = ("better", "same", "worse", "missing", "untested")
DEFAULT_TOLERANCE = {"x_m": 1.0, "depth_m": 0.3, "traces": 20}
# A point pick becomes a small box of this half size.
POINT_HALF = {"m": 0.25, "trace": 5, "depth_m": 0.1, "ns": 2.0}

DEFAULT_FEATURES = [
    "อ่านไฟล์ได้ครบ (trace, เวลา, ระยะ)",
    "ฟิลเตอร์อัตโนมัติ (dewow, bandpass, background, gain)",
    "วงโซนผิดปกติอัตโนมัติ",
    "ประเมินความเร็วคลื่นจากไฮเพอร์โบลา",
    "Migration",
    "แสดงความลึก",
    "ใช้ GPS / แผนที่",
    "Time slice / 3D",
    "รายงาน PDF / Word",
    "ประมวลผลหลายเส้นพร้อมกัน",
    "ความเร็วในการทำงาน",
    "ความง่ายในการใช้งาน",
]


def new_state() -> dict[str, Any]:
    return {"tolerance": dict(DEFAULT_TOLERANCE), "lines": {},
            "features": [{"feature": f, "source": "viewpoint", "rating": "untested", "note": ""} for f in DEFAULT_FEATURES]}


def upgrade_state(state: dict[str, Any]) -> dict[str, Any]:
    """Rename reference sources that are no longer offered (see LEGACY_SOURCES) in a saved state."""
    def fix(d: dict[str, Any]) -> None:
        if d.get("source") in LEGACY_SOURCES:
            d["source"] = LEGACY_SOURCES[d["source"]]
    for f in state.get("features", []):
        fix(f)
    for ls in state.get("lines", {}).values():
        for item in ls.get("picks", []) + ls.get("images", []):
            fix(item)
        meta = ls.get("meta", {})
        for old, new in LEGACY_SOURCES.items():
            if old in meta:
                meta.setdefault(new, meta.pop(old))
    return state


def line_state(state: dict[str, Any], line_id: str) -> dict[str, Any]:
    return state["lines"].setdefault(line_id, {"meta": {}, "picks": [], "images": [], "verdicts": {}})


# ---------------------------------------------------------------------------- geometry
@dataclass
class LineGeom:
    """What is needed to put a reference pick on our trace/time grid."""

    n_traces: int
    dx_m: float | None
    time_window_ns: float
    velocity_m_per_ns: float

    def trace(self, x: float, unit: str) -> float:
        if unit == "trace":
            return x
        if not self.dx_m:
            raise ValueError("this line has no trace spacing; give the reference position in traces")
        return x / self.dx_m

    def ns(self, y: float, unit: str, velocity: float | None) -> float:
        return y if unit == "ns" else 2.0 * y / (velocity or self.velocity_m_per_ns)

    def depth(self, ns: float) -> float:
        return ns * self.velocity_m_per_ns / 2.0


def clean_pick(d: dict[str, Any], ref_id: str) -> dict[str, Any]:
    """Validate a pick from the form, CSV or a drawn box."""
    src = LEGACY_SOURCES.get(d.get("source"), d.get("source") or "viewpoint")
    if src not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    x_unit, y_unit = d.get("x_unit", "m"), d.get("y_unit", "m")
    if x_unit not in ("m", "trace") or y_unit not in ("m", "ns"):
        raise ValueError("x_unit must be m or trace; y_unit must be m or ns")
    try:
        x0 = float(d["x_start"])
        x1 = float(d.get("x_end", x0) if d.get("x_end") not in ("", None) else x0)
        y0 = float(d["top"])
        y1 = float(d.get("bottom", y0) if d.get("bottom") not in ("", None) else y0)
    except (KeyError, TypeError, ValueError):
        raise ValueError("a reference pick needs numbers for x_start and top (x_end, bottom optional)") from None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x0 == x1:
        h = POINT_HALF[x_unit]
        x0, x1 = x0 - h, x1 + h
    if y0 == y1:
        h = POINT_HALF["depth_m" if y_unit == "m" else "ns"]
        y0, y1 = max(y0 - h, 0.0), y1 + h
    mz = d.get("match_zone") or None
    return {"ref_id": ref_id, "source": src, "x_start": x0, "x_end": x1, "x_unit": x_unit, "top": y0, "bottom": y1,
            "y_unit": y_unit, "label": str(d.get("label", ""))[:80], "ref_type": str(d.get("ref_type", ""))[:80],
            "note": str(d.get("note", ""))[:500], "match_zone": mz}


def next_ref_id(picks: list[dict[str, Any]]) -> str:
    used = {p["ref_id"] for p in picks}
    k = 1
    while f"R{k}" in used:
        k += 1
    return f"R{k}"


def pick_box(p: dict[str, Any], g: LineGeom, ref_velocity: float | None) -> tuple[float, float, float, float]:
    """(trace_start, trace_end, time_top_ns, time_bottom_ns) on our grid."""
    return (g.trace(p["x_start"], p["x_unit"]), g.trace(p["x_end"], p["x_unit"]),
            g.ns(p["top"], p["y_unit"], ref_velocity), g.ns(p["bottom"], p["y_unit"], ref_velocity))


def _gap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, a0 - b1, b0 - a1)


# ---------------------------------------------------------------------------- matching
def compare_line(ls: dict[str, Any], zones: list[dict[str, Any]], g: LineGeom,
                 tol: dict[str, float]) -> dict[str, Any]:
    """Pair reference picks with our zones; returns rows plus counts for one line.

    Our candidates are zones the reviewer has not rejected and that are not artifacts. A pick's
    match_zone forces a pairing ("none" forces no match). The user's verdicts win over the
    automatic status.
    """
    htol = tol["x_m"] / g.dx_m if g.dx_m else tol["traces"]
    vtol = g.ns(tol["depth_m"], "m", None)
    ours = [z for z in zones if z["review"] != "rejected" and z["suspected_type"] != "artifact"]
    by_id = {z["zone_id"]: z for z in zones}
    # depths are converted with the velocity the reference program used, when the user gave it
    boxes = {p["ref_id"]: pick_box(p, g, _num(ls["meta"].get(p["source"], {}).get("velocity_m_per_ns")))
             for p in ls["picks"]}

    pairs: dict[str, str | None] = {}
    taken: set[str] = set()
    for p in ls["picks"]:  # forced pairings first
        mz = p.get("match_zone")
        if mz == "none":
            pairs[p["ref_id"]] = None
        elif mz in by_id:
            pairs[p["ref_id"]] = mz
            taken.add(mz)
    cands = []
    for p in ls["picks"]:
        if p["ref_id"] in pairs:
            continue
        t0, t1, n0, n1 = boxes[p["ref_id"]]
        for z in ours:
            hg = _gap(t0, t1, z["trace_start"], z["trace_end"])
            vg = _gap(n0, n1, z["time_top_ns"], z["time_bottom_ns"])
            if hg <= htol and vg <= vtol:
                dc = abs((t0 + t1) / 2 - (z["trace_start"] + z["trace_end"]) / 2) / max(htol, 1e-9)
                cands.append((hg / max(htol, 1e-9) + vg / max(vtol, 1e-9) + 0.1 * dc, p["ref_id"], z["zone_id"]))
    for _, rid, zid in sorted(cands):
        if rid not in pairs and zid not in taken:
            pairs[rid] = zid
            taken.add(zid)

    verdicts = ls["verdicts"]
    rows = []
    for p in ls["picks"]:
        rid = p["ref_id"]
        zid = pairs.get(rid)
        t0, t1, n0, n1 = boxes[rid]
        row = {"kind": "pair" if zid else "ref_only", "ref_id": rid, "zone_id": zid, "source": p["source"],
               "label": p["label"], "ref_type": p["ref_type"], "auto": "match" if zid else "missed",
               "ref_x_start_m": _m(t0, g), "ref_x_end_m": _m(t1, g), "ref_trace_start": round(t0, 1),
               "ref_trace_end": round(t1, 1), "ref_time_top_ns": round(n0, 2), "ref_time_bottom_ns": round(n1, 2),
               # depth as the reference program shows it (its own velocity), else from time with ours
               "ref_depth_top_m": round(p["top"] if p["y_unit"] == "m" else g.depth(n0), 3),
               "ref_depth_bottom_m": round(p["bottom"] if p["y_unit"] == "m" else g.depth(n1), 3), "forced": p.get("match_zone") is not None}
        if zid:
            z = by_id[zid]
            row["our_type"] = z["suspected_type"]
            row["dx_center_m"] = _m(((z["trace_start"] + z["trace_end"]) - (t0 + t1)) / 2, g)
            row["dx_center_traces"] = round(((z["trace_start"] + z["trace_end"]) - (t0 + t1)) / 2, 1)
            row["ddepth_top_m"] = round(z.get("depth_top_m", g.depth(z["time_top_ns"])) - row["ref_depth_top_m"], 3)
        v = verdicts.get(f"ref:{rid}", {})
        row["verdict"] = v.get("verdict") or row["auto"]
        row["verdict_by_user"] = bool(v.get("verdict"))
        row["note"] = v.get("note", "")
        rows.append(row)
    for z in ours:
        if z["zone_id"] in taken:
            continue
        v = verdicts.get(f"zone:{z['zone_id']}", {})
        rows.append({"kind": "ours_only", "ref_id": None, "zone_id": z["zone_id"], "our_type": z["suspected_type"],
                     "auto": "extra", "verdict": v.get("verdict") or "extra", "verdict_by_user": bool(v.get("verdict")),
                     "note": v.get("note", "")})
    return {"rows": rows, "summary": summarize(rows), "meta_check": meta_check(ls["meta"], g)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    refs = [r for r in rows if r["kind"] != "ours_only"]
    extra = [r for r in rows if r["kind"] == "ours_only"]
    found = sum(r["verdict"] in ("match", "partial") for r in refs)
    false_extra = sum(r["verdict"] in ("extra", "extra_false") for r in extra)
    s: dict[str, Any] = {
        "n_ref": len(refs), "n_ours": len(extra) + sum(r["kind"] == "pair" for r in refs),
        "match": sum(r["verdict"] == "match" for r in refs), "partial": sum(r["verdict"] == "partial" for r in refs),
        "missed": sum(r["verdict"] == "missed" for r in refs), "unsure": sum(r["verdict"] == "unsure" for r in rows),
        "extra": len(extra), "extra_real": sum(r["verdict"] == "extra_real" for r in extra), "extra_false": false_extra,
        "recall": round(found / len(refs), 3) if refs else None,
        "precision": round(found / (found + false_extra), 3) if found + false_extra else None,
    }
    dd = [abs(r["ddepth_top_m"]) for r in refs if r.get("ddepth_top_m") is not None]
    dx = [abs(r["dx_center_m"]) for r in refs if r.get("dx_center_m") is not None]
    s["mean_abs_ddepth_m"] = round(sum(dd) / len(dd), 3) if dd else None
    s["mean_abs_dx_m"] = round(sum(dx) / len(dx), 3) if dx else None
    return s


def add_summaries(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Job-level totals from several line summaries (rates recomputed from the counts)."""
    keys = ("n_ref", "n_ours", "match", "partial", "missed", "unsure", "extra", "extra_real", "extra_false")
    tot: dict[str, Any] = {k: sum(p[k] for p in parts) for k in keys}
    found = tot["match"] + tot["partial"]
    tot["recall"] = round(found / tot["n_ref"], 3) if tot["n_ref"] else None
    tot["precision"] = round(found / (found + tot["extra_false"]), 3) if found + tot["extra_false"] else None
    return tot


def meta_check(meta: dict[str, Any], g: LineGeom) -> list[dict[str, Any]]:
    """Our line facts against what each reference program shows."""
    ours = {"n_traces": g.n_traces, "time_window_ns": g.time_window_ns,
            "length_m": (g.n_traces - 1) * g.dx_m if g.dx_m else None,
            "velocity_m_per_ns": g.velocity_m_per_ns, "max_depth_m": g.depth(g.time_window_ns)}
    out = []
    for src, vals in meta.items():
        if not isinstance(vals, dict):
            continue
        for k in META_FIELDS:
            ref = _num(vals.get(k))
            if ref is None:
                continue
            o = ours[k]
            if o is None:
                ok = None
            elif k == "n_traces":
                ok = abs(o - ref) <= 1
            elif k == "velocity_m_per_ns":
                ok = abs(o - ref) <= 0.005
            else:
                ok = abs(o - ref) <= 0.02 * max(abs(ref), 1e-9)
            out.append({"source": src, "field": k, "ref": ref, "ours": None if o is None else round(o, 4), "ok": ok})
    return out


def _m(traces: float, g: LineGeom) -> float | None:
    return round(traces * g.dx_m, 3) if g.dx_m else None


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def clean_meta(d: dict[str, Any]) -> dict[str, Any]:
    out = {k: _num(d.get(k)) for k in META_FIELDS}
    out["note"] = str(d.get("note", ""))[:500]
    return out


# ---------------------------------------------------------------------------- CSV import
# Normalised header -> field. Matching is on the header with units and punctuation removed.
ALIASES = {
    "x_start": ("x_start", "start_x", "xstart", "distance_start", "start_distance", "from", "x1", "start",
                "chainage_start", "start_m", "position_start", "ระยะเริ่ม", "ระยะเริ่มต้น"),
    "x_end": ("x_end", "end_x", "xend", "distance_end", "end_distance", "to", "x2", "end", "chainage_end", "end_m",
              "position_end", "ระยะสิ้นสุด", "ระยะจบ"),
    "x": ("x", "distance", "dist", "position", "pos", "chainage", "station", "offset", "horizontal", "along",
          "profile_position", "ระยะ", "ตำแหน่ง"),
    "trace_start": ("trace_start", "start_trace", "scan_start", "trace_from", "trace1"),
    "trace_end": ("trace_end", "end_trace", "scan_end", "trace_to", "trace2"),
    "trace": ("trace", "trace_no", "trace_number", "tracenumber", "scan", "scan_no", "scan_number"),
    "top": ("depth_top", "top_depth", "top", "z_top", "depth_from", "depth_start", "depth_min", "min_depth",
            "ความลึกบน", "ลึกบน"),
    "bottom": ("depth_bottom", "bottom_depth", "bottom", "z_bottom", "depth_to", "depth_end", "depth_max",
               "max_depth", "ความลึกล่าง", "ลึกล่าง"),
    "depth": ("depth", "z", "ความลึก", "ลึก"),
    "time_top": ("time_top", "top_time", "twt_top", "time_start", "time_from", "time_min"),
    "time_bottom": ("time_bottom", "bottom_time", "twt_bottom", "time_end", "time_to", "time_max"),
    "time": ("time", "twt", "two_way_time", "twowaytime", "travel_time"),
    "label": ("label", "name", "id", "object", "target", "annotation", "marker", "ชื่อ", "รหัส"),
    "ref_type": ("type", "class", "category", "kind", "object_type", "classification", "ประเภท", "ชนิด"),
    "note": ("note", "notes", "comment", "comments", "description", "remark", "remarks", "หมายเหตุ"),
}
FIELD_OF = {a: f for f, names in ALIASES.items() for a in names}


def _norm_header(h: str) -> tuple[str, str | None]:
    """('depth_top', 'cm') from 'Depth top (cm)'."""
    unit = None
    m = re.search(r"[\(\[]\s*([a-zA-Zµ]+)\s*[\)\]]", h)
    if m:
        unit = m.group(1).lower()
        h = h[: m.start()] + h[m.end():]
    # separators only: Thai vowel marks are not \w, so a "[^\w]" rule would break Thai names
    key = re.sub(r"[\s\-./,:;#'\"*]+", "_", h.strip().lower()).strip("_")
    for u in ("m", "cm", "mm", "ns", "ft"):  # 'depth_m', 'time_ns'
        if unit is None and key.endswith("_" + u) and key[: -len(u) - 1] in FIELD_OF:
            unit, key = u, key[: -len(u) - 1]
    return key, unit


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp874", "latin-1"):  # cp874 = Thai Windows
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def parse_picks_csv(data: bytes, source: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Reference picks from a CSV/TXT export. Returns (raw pick dicts, warnings).

    Columns are found by name (English or Thai, units in brackets understood: m, cm, mm, ns).
    A row may be a box (start/end, top/bottom) or a point (x, depth).
    """
    text = _decode(data)
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ValueError("the file is empty")
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    header = [_norm_header(h) for h in rows[0]]
    cols: dict[str, tuple[int, str | None]] = {}
    for i, (key, unit) in enumerate(header):
        f = FIELD_OF.get(key)
        if f and f not in cols:
            cols[f] = (i, unit)
    has_x = any(k in cols for k in ("x_start", "x", "trace_start", "trace"))
    has_y = any(k in cols for k in ("top", "depth", "time_top", "time"))
    if not (has_x and has_y):
        found = ", ".join(h for h in rows[0] if h.strip()) or "(none)"
        raise ValueError("could not find position and depth/time columns. Columns found: " + found +
                         ". Expected e.g. distance_start, distance_end, depth_top, depth_bottom, label "
                         "(or distance, depth for points; trace / time_ns also work)")

    def get(r: list[str], f: str) -> tuple[float | None, str | None]:
        if f not in cols or cols[f][0] >= len(r):
            return None, None
        i, unit = cols[f]
        v = r[i].strip()
        if delim != "," and re.fullmatch(r"-?\d+,\d+", v):
            v = v.replace(",", ".")
        return _num(v), unit

    def txt(r: list[str], f: str) -> str:
        return r[cols[f][0]].strip() if f in cols and cols[f][0] < len(r) else ""

    picks, warnings = [], []
    for n, r in enumerate(rows[1:], start=2):
        x_unit = "m"
        x0, ux = get(r, "x_start")
        x1, _ = get(r, "x_end")
        if x0 is None:
            x0, ux = get(r, "x")
            x1 = None
        if x0 is None:
            x0, _ = get(r, "trace_start")
            x1, _ = get(r, "trace_end")
            if x0 is None:
                x0, _ = get(r, "trace")
                x1 = None
            x_unit, ux = "trace", None
        y_unit = "m"
        y0, uy = get(r, "top")
        y1, _ = get(r, "bottom")
        if y0 is None:
            y0, uy = get(r, "depth")
            y1 = None
        if y0 is None:
            y0, uy = get(r, "time_top")
            y1, _ = get(r, "time_bottom")
            if y0 is None:
                y0, uy = get(r, "time")
                y1 = None
            y_unit = "ns"
        if x0 is None or y0 is None:
            warnings.append(f"row {n}: no position or depth, skipped")
            continue
        sx = {"cm": 0.01, "mm": 0.001, "ft": 0.3048}.get(ux or "", 1.0)
        sy = {"cm": 0.01, "mm": 0.001, "ft": 0.3048}.get(uy or "", 1.0) if y_unit == "m" else 1.0
        if uy == "ns" and y_unit == "m":  # 'depth (ns)' is really time
            y_unit, sy = "ns", 1.0
        picks.append({"source": source, "x_start": x0 * sx, "x_end": None if x1 is None else x1 * sx, "x_unit": x_unit,
                      "top": y0 * sy, "bottom": None if y1 is None else y1 * sy, "y_unit": y_unit,
                      "label": txt(r, "label"), "ref_type": txt(r, "ref_type"), "note": txt(r, "note")})
    if not picks:
        raise ValueError("no usable rows: " + "; ".join(warnings[:3]))
    return picks, warnings


TEMPLATE_CSV = ("label,type,distance_start (m),distance_end (m),depth_top (m),depth_bottom (m),note\n"
                "A1,void,12.5,13.4,0.6,1.1,example box\n"
                "A2,pipe,20.2,,0.9,,example point (end and bottom left blank)\n")


# ---------------------------------------------------------------------------- export
COMPARE_COLUMNS = ("job_id", "project", "line_id", "source_file", "kind", "source", "ref_id", "label", "ref_type",
                   "zone_id", "our_type", "auto", "verdict", "verdict_by_user", "ref_x_start_m", "ref_x_end_m",
                   "ref_trace_start", "ref_trace_end", "ref_depth_top_m", "ref_depth_bottom_m", "dx_center_m",
                   "dx_center_traces", "ddepth_top_m", "note")


def rows_to_csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    return "﻿" + buf.getvalue()  # BOM so Excel shows Thai
