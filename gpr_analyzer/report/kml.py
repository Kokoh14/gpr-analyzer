"""Google Earth export (KML / KMZ) of survey tracks and zones, from the same records as the report.

Rejected zones are left out. Zones still awaiting review go into their own folder named as
unreviewed, so a reader of the map can never mistake them for confirmed results. Coordinates are
WGS84 longitude, latitude as stored on the zones; nothing is re-projected.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from gpr_analyzer.report.figures import ACTION_COLOURS
from gpr_analyzer.report.rules import suggest_action
from gpr_analyzer.report.schema import SurveyResult, Zone


def _kml_colour(hex_rgb: str, alpha: str = "ff") -> str:
    """#rrggbb → KML aabbggrr."""
    h = hex_rgb.lstrip("#")
    return f"{alpha}{h[4:6]}{h[2:4]}{h[0:2]}"


def _coords(points) -> str:
    return " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in points)


def _zone_placemark(z: Zone, action: str) -> str:
    rows = [("line", z.line_id), ("traces", f"{z.trace_start}–{z.trace_end}"),
            ("distance (m)", "–" if z.distance_start_m is None else f"{z.distance_start_m:.1f}–{z.distance_end_m:.1f}"),
            ("depth (m)", f"{z.depth_top_m:.2f}–{z.depth_bottom_m:.2f}"),
            ("velocity (m/ns)", f"{z.velocity_m_per_ns:.3f} ({z.velocity_source})"),
            ("type", z.suspected_type), ("confidence", z.confidence), ("action", action), ("review", z.review)]
    if z.note:
        rows.append(("note", z.note))
    desc = "<table>" + "".join(f"<tr><td>{escape(k)}</td><td>{escape(str(v))}</td></tr>" for k, v in rows) + "</table>"
    pts = [z.coord_start] + ([z.coord_end] if z.coord_end and z.coord_end != z.coord_start else [])
    geom = (f"<LineString><tessellate>1</tessellate><coordinates>{_coords(pts)}</coordinates></LineString>"
            if len(pts) > 1 else f"<Point><coordinates>{_coords(pts)}</coordinates></Point>")
    return (f"<Placemark><name>{escape(f'{z.line_id}:{z.zone_id}')}</name><styleUrl>#act{action}</styleUrl>"
            f"<description><![CDATA[{desc}]]></description>{geom}</Placemark>")


def zones_kml(survey: SurveyResult, tracks: dict[str, list[tuple[float, float]]] | None = None) -> str:
    """KML text. tracks = {line_id: [(lon, lat), ...]} for the survey lines (optional)."""
    styles = "".join(
        f'<Style id="act{a}"><LineStyle><color>{_kml_colour(c)}</color><width>6</width></LineStyle>'
        f'<IconStyle><color>{_kml_colour(c)}</color></IconStyle></Style>' for a, c in ACTION_COLOURS.items())
    styles += '<Style id="track"><LineStyle><color>ff606060</color><width>2</width></LineStyle></Style>'
    parts = [f"<name>{escape(survey.info.project or 'GPR survey')}</name>", styles]
    if tracks:
        parts.append("<Folder><name>Survey lines</name>" + "".join(
            f"<Placemark><name>{escape(k)}</name><styleUrl>#track</styleUrl><LineString><tessellate>1</tessellate>"
            f"<coordinates>{_coords(v)}</coordinates></LineString></Placemark>" for k, v in tracks.items() if v)
            + "</Folder>")
    su = survey.info.surface_use
    kept = [z for z in survey.zones if z.review != "rejected" and z.coord_start is not None]
    reviewed = [z for z in kept if z.review != "auto"]
    unreviewed = [z for z in kept if z.review == "auto"]
    parts.append("<Folder><name>Zones (reviewed)</name>"
                 + "".join(_zone_placemark(z, suggest_action(z, su)) for z in reviewed) + "</Folder>")
    if unreviewed:
        parts.append("<Folder><name>Zones NOT YET REVIEWED (draft)</name>"
                     + "".join(_zone_placemark(z, suggest_action(z, su)) for z in unreviewed) + "</Folder>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + "".join(parts) + "</Document></kml>\n")


def write_kmz(survey: SurveyResult, path: str | os.PathLike | None = None,
              tracks: dict[str, list[tuple[float, float]]] | None = None) -> bytes:
    """KMZ (zipped doc.kml) bytes; written to path when given."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", zones_kml(survey, tracks).encode("utf-8"))
    data = buf.getvalue()
    if path is not None:
        Path(path).write_bytes(data)
    return data
