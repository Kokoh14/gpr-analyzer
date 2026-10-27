"""Zone table exports from the same records that feed the PDF/Word report."""

from __future__ import annotations

import csv
import io
import json
import math
import os
from pathlib import Path

from gpr_analyzer.report.rules import suggest_action
from gpr_analyzer.report.schema import SurveyResult

CSV_COLUMNS = ["zone_id", "line_id", "trace_start", "trace_end", "distance_start_m", "distance_end_m",
               "time_top_ns", "time_bottom_ns", "depth_top_m", "depth_bottom_m", "velocity_m_per_ns",
               "velocity_source", "suspected_type", "confidence", "action", "review", "artifact_flags",
               "x_start", "y_start", "x_end", "y_end", "datum", "evidence"]


def zones_csv(survey: SurveyResult, path: str | os.PathLike | None = None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for z in survey.zones:
        d = z.to_dict()
        row = {k: d.get(k) for k in CSV_COLUMNS}
        row["action"] = suggest_action(z, survey.info.surface_use)
        row["artifact_flags"] = "; ".join(z.artifact_flags)
        row["evidence"] = "; ".join(f"{e.kind}:{e.code}" for e in z.evidence)
        for key, c in (("start", z.coord_start), ("end", z.coord_end)):
            row[f"x_{key}"], row[f"y_{key}"] = (c if c else (None, None))
        row["datum"] = z.datum or survey.info.datum or None
        w.writerow(row)
    text = buf.getvalue()
    if path is not None:
        # BOM so Excel reads Thai; newline="" keeps the csv module's \r\n (else Windows writes \r\r\n = blank rows)
        Path(path).write_text(text, encoding="utf-8-sig", newline="")
    return text


def survey_json(survey: SurveyResult, path: str | os.PathLike | None = None) -> str:
    text = survey.to_json()
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


def zones_geojson(survey: SurveyResult) -> dict:
    """Zones that have coordinates, as LineString features (start → end)."""
    feats = []
    for z in survey.zones:
        if z.coord_start is None:
            continue
        coords = [list(z.coord_start)] + ([list(z.coord_end)] if z.coord_end else [])
        geom = {"type": "LineString", "coordinates": coords} if len(coords) > 1 else \
            {"type": "Point", "coordinates": coords[0]}
        props = z.to_dict()
        props["action"] = suggest_action(z, survey.info.surface_use)
        feats.append({"type": "Feature", "geometry": geom, "properties": json.loads(json.dumps(props))})
    return {"type": "FeatureCollection", "features": feats}


_CONDOR_COLORS = {"confirmed": "7950F2", "high": "E03131", "medium": "F08C00", "low": "1971C2"}
_GPS_DATUMS = {"WGS84", "WGS 84", "EPSG:4326"}


def zones_condor(survey: SurveyResult, coordinate_type: str | None = None) -> dict:
    """UTF-8 Appendix A object files and an audit manifest (no coordinate transformation).

    GPS Z is recorded surface altitude minus zone-centre depth. Missing altitudes are skipped.
    CARTESIAN Z is negative depth below a relative surface of zero, explicitly declared in manifest.
    Point and line features use distinct codes because a CNF feature has exactly one geometry type.
    """
    def datum(z):
        return (z.datum or survey.info.datum or "").strip()

    def frame(z):
        d = datum(z)
        return ("GPS" if d.upper() in _GPS_DATUMS else "CARTESIAN") if d else None

    candidates = [z for z in survey.zones if z.coord_start is not None and z.review != "rejected"]
    frames = {frame(z) for z in candidates} - {None}
    if coordinate_type is None:
        if len(frames) > 1:
            raise ValueError("Condor files cannot mix GPS and CARTESIAN; select a coordinate type")
        coordinate_type = next(iter(frames), "GPS")
    if coordinate_type not in ("GPS", "CARTESIAN"):
        raise ValueError("Condor coordinate type must be GPS or CARTESIAN")
    datums = {datum(z) for z in candidates if frame(z) == coordinate_type}
    if coordinate_type == "CARTESIAN" and len(datums) > 1:
        raise ValueError("Condor CARTESIAN export requires a single declared coordinate system")
    points = ["VERSION:2", f"COORDINATE TYPE:{coordinate_type}"]
    lines = list(points)
    definitions, exported, skipped = {}, [], []

    def name(text):
        return str(text).replace("\t", " ").replace("\r", " ").replace("\n", " ")

    def xyz(x, y, z):
        return f"{x:.9f}\t{y:.9f}\t{z:.6f}"

    for zone in survey.zones:
        reason = None
        if zone.review == "rejected":
            reason = "review rejected"
        elif zone.coord_start is None:
            reason = "no coordinates"
        elif frame(zone) != coordinate_type:
            reason = "missing or different coordinate system"
        if reason:
            skipped.append({"line_id": zone.line_id, "zone_id": zone.zone_id, "reason": reason})
            continue
        start, end = zone.coord_start, zone.coord_end or zone.coord_start
        if len(start) != 2 or len(end) != 2 or not all(math.isfinite(v) for v in (*start, *end)):
            raise ValueError(f"{zone.line_id}:{zone.zone_id}: coordinates must be finite XY pairs")
        if coordinate_type == "GPS" and any(abs(x) > 180 or abs(y) > 90 for x, y in (start, end)):
            raise ValueError("GPS coordinates must be longitude, latitude in degrees")
        surface = (zone.surface_elevation_start_m, zone.surface_elevation_end_m if zone.coord_end else zone.surface_elevation_start_m)
        if coordinate_type == "GPS" and not all(v is not None and math.isfinite(v) for v in surface):
            skipped.append({"line_id": zone.line_id, "zone_id": zone.zone_id, "reason": "GPS surface altitude missing; target altitude cannot be inferred from depth alone"})
            continue
        if not math.isfinite(zone.velocity_m_per_ns) or zone.velocity_m_per_ns <= 0:
            raise ValueError("zone velocity must be finite and positive")
        depth = (zone.depth_top_m + zone.depth_bottom_m) / 2
        if not math.isfinite(depth):
            raise ValueError("zone depth must be finite")
        z0, z1 = (surface[0] - depth, surface[1] - depth) if coordinate_type == "GPS" else (-depth, -depth)
        xc = (start[0] + end[0]) / 2
        if coordinate_type == "GPS":
            xc = (start[0] + ((end[0] - start[0] + 180) % 360 - 180) / 2 + 180) % 360 - 180
        yc, zc = (start[1] + end[1]) / 2, (z0 + z1) / 2
        code = f"GPR_{zone.suspected_type}_{zone.confidence}"
        label = name(f"{zone.line_id}:{zone.zone_id} [{zone.review}]")
        for kind, suffix, target, coords in ((1, "P", points, [(xc, yc, zc)]),
                                             (2, "L", lines, [(*start, z0), (*end, z1)])):
            if kind == 2 and (zone.coord_end is None or start == end):
                continue
            feature = f"{code}_{suffix}"
            definitions[feature] = f"{kind}\t2\t{feature}\t{_CONDOR_COLORS[zone.confidence]}\t25CF\t2\t0.5"
            target.extend(["---", f"{feature}\t{label}", *(xyz(*c) for c in coords)])
        exported.append({"line_id": zone.line_id, "zone_id": zone.zone_id, "velocity_m_per_ns": zone.velocity_m_per_ns,
                         "depth_top_m": zone.depth_top_m, "depth_bottom_m": zone.depth_bottom_m,
                         "target_depth_m": depth, "review": zone.review, "datum": datum(zone)})
    if not exported:
        reasons = "; ".join(sorted({s["reason"] for s in skipped}))
        raise ValueError(f"no zones eligible for Condor export: {reasons or 'no zones'}")
    return {"cnf": "\n".join(["VERSION:1", *(definitions[k] for k in sorted(definitions))]) + "\n",
            "pobj": "\n".join(points) + "\n", "lobj": "\n".join(lines) + "\n",
            "manifest": {"coordinate_type": coordinate_type, "datums": sorted(datums),
                         "z_reference": "recorded surface altitude minus target depth; vertical datum unverified" if coordinate_type == "GPS" else "relative surface 0 m minus target depth",
                         "depth_definition": "midpoint of zone top/bottom depths using the zone's own velocity and antenna separation",
                         "exported": exported, "skipped": skipped,
                         "limitation": "proposed interpretation; Condor import needs licensed manual verification"}}


def condor_zip(survey: SurveyResult, path: str | os.PathLike) -> Path:
    """A matched set of definitions/objects and an explicit skipped-zone/depth manifest."""
    import zipfile

    bundle = zones_condor(survey)
    path = Path(path)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for ext in ("cnf", "pobj", "lobj"):
            archive.writestr(f"zones.{ext}", bundle[ext].encode("utf-8"))
        archive.writestr("manifest.json", json.dumps(bundle["manifest"], ensure_ascii=False, indent=2).encode("utf-8"))
    return path
