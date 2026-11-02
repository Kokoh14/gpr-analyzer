"""Sort a batch of uploaded files into readable lines and companion files.

Several formats come as a pair (ImpulseRadar .iprh + .iprb, MALA .rad + .rd3, S&S .HD + .DT1).
The readers find the partner next to the file they are given, so every upload goes into one
folder with its original name and each pair is read once, from its header file.
Anything else (.txt notes, .cor GPS, .mrk markers, unknown binaries) is kept as a companion
and attached to the line with the same file stem.
IDS zones are the exception: all LIccpppp.dt files of one folder (channels and parts) are one line,
named after the folder, the other files of that folder are its companions, and files in its
sub-folders (camera pictures, SRSv2_Data) are left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from gpr_analyzer.io.ids import is_ids_dt, zone_name

# extension -> (family, preference); within a family and stem the lowest preference is read
FAMILIES = {
    ".iprh": ("impulseradar", 0), ".iprb": ("impulseradar", 1),
    ".rad": ("mala", 0), ".rd3": ("mala", 1), ".rd7": ("mala", 1),
    ".hd": ("sensoft", 0), ".dt1": ("sensoft", 1),
    ".dzt": ("gssi", 0),
    ".sgy": ("segy", 0), ".segy": ("segy", 0),
    ".dt": ("ids", 0),
}
TEXT_EXTENSIONS = {".txt", ".csv", ".ini", ".xml", ".json", ".log"}


def safe_name(name: str) -> str:
    """File name without any directory part or characters that are unsafe on disk."""
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ]", "_", base, flags=re.UNICODE).strip(" .")
    return base or "upload.bin"


@dataclass
class InputGroup:
    """One line to read: the file handed to the reader plus every file that shares its stem."""

    stem: str
    family: str
    primary: Path
    members: list[Path] = field(default_factory=list)
    companions: list[Path] = field(default_factory=list)


@dataclass
class Sorted:
    groups: list[InputGroup]
    unassigned: list[Path]  # companions with no matching line (shown to the user, never read)


def sort_files(paths: list[Path]) -> Sorted:
    groups: dict[tuple[str, str], InputGroup] = {}
    others: list[Path] = []
    zones: dict[Path, InputGroup] = {}
    for p in sorted(paths, key=lambda q: q.name.lower()):
        if is_ids_dt(p):
            g = zones.get(p.parent)
            if g is None:
                g = zones[p.parent] = InputGroup(stem=zone_name(p), family="ids", primary=p)
            g.members.append(p)
            continue
        fam = FAMILIES.get(p.suffix.lower())
        if fam is None or fam[0] == "ids":
            others.append(p)
            continue
        key = (p.stem.lower(), fam[0])
        g = groups.get(key)
        if g is None:
            groups[key] = InputGroup(stem=p.stem, family=fam[0], primary=p, members=[p])
        else:
            g.members.append(p)
            if fam[1] < FAMILIES[g.primary.suffix.lower()][1]:
                g.primary = p
    by_stem: dict[str, list[InputGroup]] = {}
    for g in groups.values():
        by_stem.setdefault(g.stem.lower(), []).append(g)
    unassigned = []
    for p in others:
        zone = next((zones[d] for d in p.parents if d in zones), None)
        if zone is not None:
            if p.parent in zones:
                zone.companions.append(p)
            continue
        owners = by_stem.get(p.stem.lower())
        if owners:
            owners[0].companions.append(p)
        else:
            unassigned.append(p)
    return Sorted(groups=list(groups.values()) + list(zones.values()), unassigned=unassigned)


def preview_text(path: Path, limit: int = 4000) -> str | None:
    """First bytes of a text companion, decoded leniently; None for binary files."""
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return None
    with path.open("rb") as stream:
        raw = stream.read(limit)
    for enc in ("utf-8", "cp874", "latin-1"):  # cp874 = Thai Windows
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None
