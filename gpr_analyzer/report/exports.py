"""Zone table exports from the same records that feed the PDF/Word report."""

from __future__ import annotations

import csv
import io
import json
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
        Path(path).write_text(text, encoding="utf-8-sig")  # BOM so Excel reads Thai
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
