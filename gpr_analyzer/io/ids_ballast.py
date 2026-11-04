"""IDS automatic ballast CSVs (LIccpppp_automatic.csv), matched to the traces of an IDS zone.

Layout, from the user's own files: one CSV per .dt part and channel, a header row, then one row about
every 5 m: X1[m];X2[m];Moisture at the base of ballast;Ballast moisture;Ballast fouling;Undulation of
Ballast. X is a running distance (the zone's start distance plus the wheel distance). 255 = no value.
What the class numbers mean is not stated in the files, so they are shown as they are.

The CSVs sit in folders named by km range, not by zone, and every folder restarts at LIcc0001, so a zone
is matched by shape: the folder must hold a CSV for every part, and each CSV must span about as far as
its part (the last part's length differs from zone to zone).
"""

from __future__ import annotations

import re
from pathlib import Path

_NAME = re.compile(r"^LI(\d\d)(\d{4})_automatic\.csv$", re.IGNORECASE)
MAX_SPAN_ERROR_M = 12.0  # real CSVs end about 5 m short of their part


def read_csv(path: Path) -> list[list[float]]:
    """[[x1, x2, moisture_base, ballast_moisture, fouling, undulation], ...]"""
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        v = [s for s in line.strip().split(";") if s != ""]
        if len(v) < 6:
            continue
        try:
            rows.append([float(v[0]), float(v[1])] + [int(float(x)) for x in v[2:6]])
        except ValueError:
            continue
    return rows


def index(root: str | Path) -> dict[Path, dict[tuple[int, int], Path]]:
    """{folder: {(channel, part): csv}} for every folder under root holding automatic CSVs."""
    out: dict[Path, dict[tuple[int, int], Path]] = {}
    for p in Path(root).expanduser().rglob("*.csv"):
        m = _NAME.match(p.name)
        if m:
            out.setdefault(p.parent, {})[(int(m.group(1)), int(m.group(2)))] = p
    return out


def zone_rows(idx: dict[Path, dict[tuple[int, int], Path]], channel: int, part_traces: list[int],
              dx: float) -> tuple[list[list[float]], Path | None]:
    """(rows as [trace_start, trace_end, moisture_base, ballast_moisture, fouling, undulation], folder).

    Traces count from the zone's first trace. No match gives ([], None).
    """
    best, best_err = None, MAX_SPAN_ERROR_M
    for folder, files in idx.items():
        if not all((channel, k + 1) in files for k in range(len(part_traces))):
            continue
        err, parts = 0.0, []
        for k, n in enumerate(part_traces):
            rows = read_csv(files[(channel, k + 1)])
            if not rows:
                err = float("inf")
                break
            span = abs(rows[-1][1] - rows[0][0])
            err = max(err, abs(span - n * dx))
            parts.append(rows)
        if err < best_err:
            best, best_err = (folder, parts), err
    if best is None:
        return [], None
    folder, parts = best
    out, offset = [], 0
    for rows, n in zip(parts, part_traces):
        sign = 1.0 if rows[-1][0] >= rows[0][0] else -1.0
        base = rows[0][0] - sign * dx  # the first row starts one trace into the part
        for x1, x2, *vals in rows:
            t0, t1 = sorted((round(sign * (x1 - base) / dx), round(sign * (x2 - base) / dx)))
            if t1 >= 0 and t0 < n:
                out.append([offset + max(t0, 0), offset + min(t1, n - 1), *vals])
        offset += n
    return out, folder


def crop_rows(rows: list[list[float]], t0: int, t1: int) -> list[list[float]]:
    """Rows inside traces t0..t1 of the zone, renumbered from t0."""
    return [[max(a, t0) - t0, min(b, t1) - t0, *v] for a, b, *v in rows if b >= t0 and a <= t1]
