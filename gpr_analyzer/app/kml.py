"""Google Earth KML/KMZ in the layout ImpulseRadar ViewPoint exports, read and written.

Fitted outside-in on one real ViewPoint 1.4.18 export (MEA_GPR_POC_2026-10-22.kml/.kmz, 59 profiles,
6 markers) against the raw files it was made from. No vendor code was read. What the export holds:

- one <Folder> per profile, named after the file stem without the channel suffix (<project>_0057);
- in it a green LineString "Track for profile: ..." whose vertices are exactly the .cor GPS fixes
  (lon,lat,0; numbers written in shortest form, trailing zeros dropped);
- a sub-folder "Markers: <profile>" with one Point per .mrk marker, named "Mark: <profile number>.<k>",
  styled by colour standard and index (#APWA:2 = red), with an HTML balloon: colour name, APWA meaning,
  lat/lon/elevation from the .mrk, target depth at the marker's sample (core.depth_from_time with the
  header's ZERO LEVEL, SOIL VELOCITY and ANTENNA SEPARATION, 2 decimals) and the GPS quality ("Status");
- styles for the APWA, AS5488 and JUPEM marker colours plus "greenTrack", always all of them;
- CRLF line ends, LF between coordinates, no newline after </kml>.

The KMZ is a zip of that KML with AutoScreenShots/<mark>.jpg (one picture per marker) and logo.png.
ViewPoint orders the profile folders in no meaningful order; we write them sorted.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any

from gpr_analyzer.core import depth_from_time

ICON = "http://maps.google.com/mapfiles/kml/pal2/icon18.png"
ZONES_FOLDER = "GPR Analyzer zones"
KML_NOTE = "ViewPoint KML"  # note on reference picks imported from a KML, so a new import replaces them
TRACK_STYLE = "greenTrack"
TRACK_COLOUR = "FF00ff00"  # KML colours are aabbggrr

# Marker colour standards as listed in the ViewPoint export, KML aabbggrr per index.
STANDARDS = {
    "APWA": ["FFFFFFFF", "FF9553DC", "FF2330EE", "FF00F1FD", "FF2097FA", "FFC37B00", "FF95229A", "FF6A9902"],
    "AS5488": ["FFFFFFFF", "FF4FD192", "FF2F81E6", "FF0002FD", "FF01FFFF", "FF023167", "FFA02F72", "FFCCF2FF",
               "FFCA31FE", "FFC17102"],
    "JUPEM": ["FF0002FD", "FFC17102", "FF01FFFF", "FF2F81E6", "FF4FD192", "FFFFFFFF"],
}
# APWA uniform colour code (public standard). Only "Red" is confirmed by the ViewPoint file itself.
APWA = [
    ("White", "Proposed Excavation"),
    ("Pink", "Temporary Survey Markings"),
    ("Red", "Electric Power Lines, Cables, Conduit and Lighting Cables"),
    ("Yellow", "Gas, Oil, Steam, Petroleum or Gaseous Materials"),
    ("Orange", "Communication, Alarm or Signal Lines, Cables or Conduit"),
    ("Blue", "Potable Water"),
    ("Purple", "Reclaimed Water, Irrigation and Slurry Lines"),
    ("Green", "Sewers and Drain Lines"),
]


def css_colour(kml_colour: str) -> str:
    """'FF2330EE' (aabbggrr) -> '#EE3023'."""
    c = kml_colour.upper()
    return f"#{c[6:8]}{c[4:6]}{c[2:4]}"


def mark_colour(m: "Mark") -> str:
    """CSS colour of a marker's style, white when the style is unknown."""
    colours = STANDARDS.get(m.standard, [])
    return css_colour(colours[m.index] if 0 <= m.index < len(colours) else "FFFFFFFF")


def num(v: float) -> str:
    """Shortest form, as in the ViewPoint file (13.5602538090 -> 13.560253809, -27.50 -> -27.5)."""
    return repr(float(v))


@dataclass
class Mark:
    name: str  # "Mark: 57.1"
    lat: float
    lon: float
    standard: str = "APWA"
    index: int = 0  # colour index in the standard
    alt: float | None = None
    depth_m: float | None = None
    status: str = ""  # GPS quality from the .mrk / .cor
    image: str = ""  # path inside the KMZ, e.g. AutoScreenShots/57.1.jpg
    type_name: str = ""  # colour name, e.g. Red
    information: str = ""  # what the colour means, e.g. Electric Power Lines...
    extra: list[tuple[str, str]] = field(default_factory=list)  # more <h3> rows (ours only)


@dataclass
class Profile:
    name: str
    track: list[tuple[float, float]]  # (lat, lon)
    marks: list[Mark] = field(default_factory=list)  # field markers (.mrk), as ViewPoint exports them
    zones: list[Mark] = field(default_factory=list)  # our zones, in their own sub-folder (not in ViewPoint's file)


def profile_number(name: str) -> str:
    """'MEA_GPR_POC_2026-10-22_0057' -> '57'."""
    m = re.search(r"(\d+)$", name)
    return str(int(m.group(1))) if m else name


def apwa(index: int) -> tuple[str, str]:
    return APWA[index] if 0 <= index < len(APWA) else ("", "")


def profile_name(source_file: str) -> str:
    """'MEA_..._0057_0.iprh' -> 'MEA_..._0057' (both channels of a line are one profile)."""
    stem = source_file.rsplit(".", 1)[0]
    return stem[:-2] if len(stem) > 2 and stem[-2] == "_" and stem[-1] in "01" else stem


def marker_depth(rg: Any, sample: int) -> float | None:
    """Depth ViewPoint prints for a marker: from ZERO LEVEL, bistatic, cut (not rounded) to the centimetre.

    Matches all 6 markers of the real export; rounding instead puts two of them 1 cm deeper.
    """
    md = rg.metadata
    v = md.get("velocity_m_per_ns") or rg.velocity_m_per_ns
    if not v:
        return None
    t = (sample - md.get("zero_level_sample", 0)) * rg.dt_ns
    d = float(depth_from_time(t, v, md.get("antenna_separation_m", 0.0)))
    return math.floor(d * 100 + 1e-6) / 100


def field_profile(rg: Any, name: str) -> Profile:
    """The profile as ViewPoint exports it: .cor track and the field markers of this channel's .mrk."""
    md = rg.metadata
    track = [(g["lat"], g["lon"]) for g in md.get("gps") or [] if g.get("lat") is not None]
    marks = []
    n = profile_number(name)
    for k, m in enumerate((m for m in md.get("markers") or [] if m.get("lat") is not None), 1):
        idx = int(m["status"]) if str(m.get("status", "")).isdigit() else 0
        std = m.get("type") or "APWA"
        tname, info = apwa(idx) if std == "APWA" else ("", "")
        marks.append(Mark(name=f"Mark: {n}.{k}", lat=m["lat"], lon=m["lon"], standard=std, index=idx,
                          alt=m.get("elevation"), depth_m=marker_depth(rg, m["sample"]), status=m.get("quality", ""),
                          image=f"AutoScreenShots/{n}.{k}.jpg", type_name=tname, information=info))
    return Profile(name=name, track=track, marks=marks)


# ---------------------------------------------------------------------------- a job's lines -> profiles
# Zones exported as points. APWA pink ("temporary survey markings") is used for all of them: the program
# finds a target but does not know what the utility carries; the reviewer's type is in the balloon.
ZONE_TYPES = ("pipe_point", "void_like", "scattered", "unknown")
ZONE_STYLE = ("APWA", 1)


def _nearest_fix(gps: list[dict[str, Any]], trace: int) -> dict[str, Any]:
    return min(gps, key=lambda g: abs(g["trace"] - trace)) if gps else {}


def job_profiles(lines: list[Any]) -> tuple[list[Profile], dict[str, tuple[Any, int, float]]]:
    """Profiles for a job's processed lines (app.jobs.LineState), and the picture each marker needs:
    image path -> (line, trace, time ns) for snapshot_jpg.

    Both channels of a dual-channel line are one profile. Track and field markers come from the
    high-frequency channel (_0, as ViewPoint), zones from every channel.
    """
    groups: dict[str, list[Any]] = {}
    for ln in lines:
        if ln.result is not None:
            groups.setdefault(profile_name(ln.source_file), []).append(ln)
    profiles, shots = [], {}
    for name in sorted(groups):
        lns = sorted(groups[name], key=lambda x: x.source_file)
        p = field_profile(lns[0].raw, name)
        zero = lns[0].raw.metadata.get("zero_level_sample", 0)
        for m, mk in zip(p.marks, (m for m in lns[0].raw.metadata.get("markers") or [] if m.get("lat") is not None)):
            shots[m.image] = (lns[0], mk["trace"], (mk["sample"] - zero) * lns[0].raw.dt_ns)
        n = profile_number(name)
        k = 0
        for ln in lns:
            pos = ln.positions()
            if pos is None:
                continue
            gps = ln.raw.metadata.get("gps") or []
            mhz = ln.raw.metadata.get("antenna_frequency_mhz")
            for z in sorted(ln.zones, key=lambda z: z.trace_start):
                if z.review == "rejected" or ln.excluded(z) or z.suspected_type not in ZONE_TYPES:
                    continue
                k += 1
                c = min(max((z.trace_start + z.trace_end) // 2, 0), len(pos[0]) - 1)
                fix = _nearest_fix(gps, c)
                tname, info = apwa(ZONE_STYLE[1])
                img = f"AutoScreenShots/GPR_{n}.{k}.jpg"
                p.zones.append(Mark(
                    name=f"GPR: {n}.{k}", lat=float(pos[0][c]), lon=float(pos[1][c]), standard=ZONE_STYLE[0],
                    index=ZONE_STYLE[1], alt=fix.get("elevation"), depth_m=z.depth_top_m, status=fix.get("quality", ""),
                    image=img, type_name=tname, information=info,
                    extra=[("Zone", f"{ln.line_id} {z.zone_id}" + (f" ({mhz:g} MHz)" if mhz else "")),
                           ("Suspected type", z.suspected_type), ("Confidence", z.confidence), ("Review", z.review)]))
                shots[img] = (ln, c, z.time_top_ns)
        profiles.append(p)
    return profiles, shots


def map_marks(lines: list[Any], reference: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Markers for the map, drawn like Google Earth shows the ViewPoint file.

    field: each line's .mrk markers (high-frequency channel), named and coloured as ViewPoint exports them.
    reference: markers of an imported answer-key KML (Job.import_ref_kml), with their own colours.
    """
    field_marks = []
    seen = set()
    for ln in sorted(lines, key=lambda x: x.source_file):
        name = profile_name(ln.source_file)
        if name in seen or ln.result is None:
            continue
        seen.add(name)
        p = field_profile(ln.raw, name)
        field_marks += [{"profile": name, "line_id": ln.line_id, "name": m.name, "lat": m.lat, "lon": m.lon,
                         "depth_m": m.depth_m, "type": m.type_name, "information": m.information,
                         "colour": mark_colour(m)} for m in p.marks]
    ref = [{"profile": p["name"], **m} for p in reference or [] for m in p["marks"]]
    return {"field": field_marks, "reference": ref}


def mark_trace(m: Mark, pos: tuple[Any, Any], raw_markers: list[dict[str, Any]] | None) -> int:
    """Trace of a KML marker on our line.

    The raw .mrk comes first: ViewPoint writes the marker's position as the GPS fix nearest to it, so a
    .mrk marker at the same position gives the exact trace. Otherwise the trace nearest the position on
    our track; where several traces share it (before the first fix, or between fixes) the middle one.
    """
    for r in raw_markers or []:
        if r.get("lat") is not None and abs(r["lat"] - m.lat) < 1e-9 and abs(r["lon"] - m.lon) < 1e-9:
            return int(r["trace"])
    import numpy as np

    d = (pos[0] - m.lat) ** 2 + ((pos[1] - m.lon) * np.cos(np.radians(m.lat))) ** 2
    best = np.flatnonzero(d <= d.min() + 1e-18)
    return int(best[len(best) // 2])


def snapshot_jpg(ln: Any, trace: int, t_ns: float, half_m: float = 2.5) -> bytes:
    """Our ViewPoint-style radargram around a marker, with the marker circled (like AutoScreenShots)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    res = ln.result
    rg = res.viewpoint or res.analysis
    d = rg.data[:res.analysis.n_samples]
    half = int(half_m / rg.dx_m) if rg.dx_m else 130
    a, b = max(trace - half, 0), min(trace + half, d.shape[1] - 1)
    sub = d[:, a:b + 1]
    c = float(np.percentile(np.abs(sub), 98.0)) or 1.0
    fig = plt.figure(figsize=(5, 3), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(sub, cmap="gray", vmin=-c, vmax=c, aspect="auto", interpolation="bilinear")
    ax.plot([trace - a], [t_ns / rg.dt_ns], "o", ms=14, mfc="none", mec="#e03131", mew=2.5)
    ax.set_axis_off()
    buf = io.BytesIO()
    fig.savefig(buf, format="jpg", dpi=100)
    plt.close(fig)
    return buf.getvalue()


def logo_png() -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(2, 0.5), dpi=100)
    fig.text(0.5, 0.5, "GPR Analyzer", ha="center", va="center", fontsize=14, weight="bold", color="#1f6feb")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------- writing
def _style(sid: str, colour: str) -> list[str]:
    return [f'    <Style id="{sid}">', "      <IconStyle>", f"        <color>{colour}</color>",
            "        <scale>0.75</scale>", "        <Icon>", f"          <href>{ICON}</href>", "        </Icon>",
            "      </IconStyle>", "      <LabelStyle>", "        <scale>0</scale>", "      </LabelStyle>",
            "      <BalloonStyle>", "        <text>$[description]</text>", "      </BalloonStyle>", "    </Style>"]


def _balloon(m: Mark) -> str:
    rows = [("Type", m.type_name), ("Information", m.information), ("Standard", m.standard)]
    h = f'<h1 style="text-align:center">{_esc(m.name)}</h1>'
    h += "".join(f"<h3>{k}: {_esc(v)}</h3>" for k, v in rows)
    h += f"<h3>Lat: {num(m.lat)} Lon: {num(m.lon)} Alt: {'' if m.alt is None else num(m.alt)}</h3>"
    h += f"<h3>Target Depth ([m]): {'' if m.depth_m is None else num(round(m.depth_m, 2))}</h3>"
    h += "".join(f"<h3>{_esc(k)}: {_esc(v)}</h3>" for k, v in m.extra)
    h += f"<h3>Status: {_esc(m.status)}</h3>"
    if m.image:
        h += f"<img src='{m.image}' width='500'/>"
    h += "<p style=\"text-align:center;\"><img src='logo.png' width='100'/></p>"
    return h


def _esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_kml(profiles: list[Profile]) -> str:
    out = ["<?xml version='1.0' encoding='UTF-8' ?>", "<kml>", "  <Document>"]
    for std, colours in STANDARDS.items():
        for i, c in enumerate(colours):
            out += _style(f"{std}:{i}", c)
    out += [f'    <Style id="{TRACK_STYLE}">', "      <LineStyle>", f"        <color>{TRACK_COLOUR}</color>",
            "        <width>4</width>", "      </LineStyle>", "      <PolyStyle>",
            f"        <color>{TRACK_COLOUR}</color>", "      </PolyStyle>", "    </Style>"]
    for p in profiles:
        name = _esc(p.name)
        out += ["    <Folder>", f"      <name>{name}</name>"]
        if len(p.track) >= 2:
            coords = "".join(f"{num(lon)},{num(lat)},0\n" for lat, lon in p.track)
            out += ["      <Placemark>", f"        <name>{name}</name>",
                    f"        <description>Track for profile: {name}</description>",
                    f"        <styleUrl>#{TRACK_STYLE}</styleUrl>", "        <LineString>",
                    "          <extrude>1</extrude>", "          <tessellate>1</tessellate>",
                    "          <altitudeMode>clampToGround</altitudeMode>", f"          <coordinates>{coords}</coordinates>",
                    "        </LineString>", "      </Placemark>"]
        for title, marks in ((f"Markers: {name}", p.marks), (f"{ZONES_FOLDER}: {name}", p.zones)):
            if title.startswith(ZONES_FOLDER) and not marks:
                continue
            out += ["      <Folder>", f"        <name>{title}</name>"]
            for m in marks:
                out += ["        <Placemark>", f"          <name>{_esc(m.name)}</name>",
                        f"          <description><![CDATA[{_balloon(m)}]]>", "          </description>",
                        f"          <styleUrl>#{m.standard}:{m.index}</styleUrl>", "          <Point>",
                        f"            <coordinates>{num(m.lon)},{num(m.lat)},0</coordinates>", "          </Point>",
                        "        </Placemark>"]
            out += ["      </Folder>"]
        out += ["    </Folder>"]
    out += ["  </Document>", "</kml>"]
    return "\r\n".join(out)


def build_kmz(kml_name: str, kml_text: str, images: dict[str, bytes], logo: bytes | None = None) -> bytes:
    """images: path inside the zip (AutoScreenShots/57.1.jpg) -> JPEG bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in images.items():
            z.writestr(path, data)
        if logo:
            z.writestr("logo.png", logo)
        z.writestr(kml_name, kml_text.encode("utf-8"))
    return buf.getvalue()


# ---------------------------------------------------------------------------- reading
_H3 = re.compile(r"<h3>\s*([^:<]+?)\s*:\s*(.*?)\s*</h3>", re.S)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _text(el: ET.Element, name: str) -> str:
    c = next(iter(_children(el, name)), None)
    return (c.text or "").strip() if c is not None else ""


def _coords(el: ET.Element) -> list[tuple[float, float]]:
    c = next((x for x in el.iter() if _local(x.tag) == "coordinates"), None)
    pts = []
    for tok in (c.text or "").split() if c is not None else []:
        parts = tok.split(",")
        if len(parts) >= 2:
            pts.append((float(parts[1]), float(parts[0])))
    return pts


def _mark(pm: ET.Element) -> Mark | None:
    pts = _coords(pm)
    if not pts:
        return None
    desc = _text(pm, "description")
    f = {k.strip(): v.strip() for k, v in _H3.findall(desc)}
    std, _, idx = _text(pm, "styleUrl").lstrip("#").partition(":")
    lat, lon = pts[0]
    depth = f.get("Target Depth ([m])") or next((v for k, v in f.items() if k.startswith("Target Depth")), "")
    img = re.search(r"<img src='(AutoScreenShots/[^']+)'", desc)
    alt = re.search(r"Alt:\s*(-?[\d.]+)", f.get("Lat", ""))
    try:
        return Mark(name=_text(pm, "name"), lat=lat, lon=lon, standard=std or f.get("Standard", ""),
                    index=int(idx) if idx.isdigit() else 0, alt=float(alt.group(1)) if alt else None,
                    depth_m=float(depth) if depth else None, status=f.get("Status", ""),
                    image=img.group(1) if img else "", type_name=f.get("Type", ""),
                    information=f.get("Information", ""))
    except ValueError:
        return None


def parse(data: bytes) -> list[Profile]:
    """Profiles of a ViewPoint KML, or of the KML inside a KMZ."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if name is None:
                raise ValueError("the KMZ holds no .kml file")
            data = z.read(name)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"not a KML file: {e}") from None
    profiles = []
    for folder in (el for el in root.iter() if _local(el.tag) == "Folder"):
        name = _text(folder, "name")
        if name.startswith(("Markers:", ZONES_FOLDER)):
            continue
        track = next((pm for pm in _children(folder, "Placemark")
                      if any(_local(x.tag) == "LineString" for x in pm.iter())), None)
        marks = []
        for sub in _children(folder, "Folder"):
            if _text(sub, "name").startswith(ZONES_FOLDER):
                continue  # our own zones in a file we exported earlier: not reference markers
            for pm in _children(sub, "Placemark"):
                m = _mark(pm)
                if m:
                    marks.append(m)
        if track is None and not marks:
            continue
        profiles.append(Profile(name=name, track=_coords(track) if track is not None else [], marks=marks))
    return profiles


__all__ = ["Mark", "Profile", "STANDARDS", "APWA", "build_kml", "build_kmz", "parse", "css_colour", "apwa",
           "profile_number", "profile_name", "marker_depth", "field_profile", "job_profiles", "snapshot_jpg",
           "logo_png", "num"]
