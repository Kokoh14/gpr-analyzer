"""Compare our tracks with ImpulseRadar ViewPoint marker screenshots of the same lines.

    python scripts/fit_viewpoint_screens.py "<survey folder>" [--out scores.csv]

<survey folder> holds the raw <project>_<line>_<ch>.iprh/.iprb/.mrk files and
Export/AutoScreenShots/<line>.<marker>.jpg as ViewPoint writes them. Nothing is uploaded or copied;
the script prints one row per screenshot panel and the mean per channel. Needs Pillow.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

from gpr_analyzer.core import depth_from_time
from gpr_analyzer.io.impulseradar import read_impulseradar
from gpr_analyzer.processing import filters as F
from gpr_analyzer.processing.auto import ProcessingOptions, run_pipeline
from gpr_analyzer.processing.screen_fit import compare, load_screenshot, register

TRACKS = ("analysis", "display", "viewpoint")


def _zero(res) -> float:
    for s in res.log:
        if s.step == "time_zero":
            return float(s.params.get("time_zero_ns", 0.0)) / res.raw.dt_ns
    return 0.0


def score_folder(folder: Path, options: ProcessingOptions | None = None) -> list[dict]:
    shots = sorted((folder / "Export" / "AutoScreenShots").glob("*.jpg"))
    cache: dict[tuple[str, int], tuple] = {}
    rows = []
    for shot in shots:
        parts = shot.stem.split(".")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            continue
        line, mk = f"{int(parts[0]):04d}", int(parts[1])
        for panel in load_screenshot(shot):
            ch = panel.channel
            hdr = next(folder.glob(f"*_{line}_{ch}.iprh"), None)
            if hdr is None:
                continue
            if (line, ch) not in cache:
                rg = read_impulseradar(hdr)[0]
                res = run_pipeline(rg, options)
                d = rg.data - rg.data.mean(0)
                f = rg.metadata.get("antenna_frequency_mhz") or 400.0
                ref = F.background_removal(F.dewow(d, rg.dt_ns, 2e3 / f), None, "mean")
                cache[(line, ch)] = (rg, res, ref)
            rg, res, ref = cache[(line, ch)]
            marks = rg.metadata.get("markers", [])
            if len(marks) < mk:
                continue
            m = marks[mk - 1]
            try:
                reg, rc = register(panel, ref, (m["trace"], m["sample"]), rg.dx_m)
            except ValueError as e:
                print(f"{shot.name} ch{ch}: skipped ({e})", file=sys.stderr)
                continue
            row = {"shot": shot.name, "channel": ch, "px_per_sample": round(reg.px_per_sample, 3),
                   "registration_corr": round(rc, 3),
                   # depth as ViewPoint reports it: header soil velocity, bistatic geometry
                   "marker_depth_m": round(float(depth_from_time((m["sample"] - _zero(res)) * rg.dt_ns, rg.velocity_m_per_ns,
                                                                 rg.metadata.get("antenna_separation_m", 0.0))), 3)}
            for t in TRACKS:
                sc = compare(panel, reg, getattr(res, t).data, _zero(res))
                for k, v in sc.items():
                    row[f"{t}_{k}"] = round(v, 3)
            rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--min-registration", type=float, default=0.3,
                    help="drop panels whose registration correlation is below this (circle not found / covered)")
    a = ap.parse_args(argv)
    rows = [r for r in score_folder(a.folder) if r["registration_corr"] >= a.min_registration]
    if not rows:
        print("no screenshot could be matched", file=sys.stderr)
        return 1
    keys = list(rows[0])
    print("\t".join(keys))
    for r in rows:
        print("\t".join(str(r[k]) for k in keys))
    for ch in (0, 1):
        sub = [r for r in rows if r["channel"] == ch]
        if sub:
            print(f"channel {ch} mean over {len(sub)} panels: " + "  ".join(
                f"{t}: r_row={np.mean([r[f'{t}_row_corr'] for r in sub]):.3f} "
                f"grey_rmse={np.mean([r[f'{t}_grey_rmse'] for r in sub]):.1f}" for t in TRACKS))
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, keys)
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
