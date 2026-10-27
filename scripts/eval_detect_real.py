"""Run the automatic detection on a real ImpulseRadar survey folder and measure it honestly.

    python scripts/eval_detect_real.py "G:\\...\\Sample Data" --out eval_out
    python scripts/eval_detect_real.py "<survey>" --labels "<app work folder>\\<job>\\labels.jsonl" --out eval_out

Writes to --out: lines.csv (zone counts per line, artifact flags, velocity, run time), zones.csv (every
proposed zone with its reasons and cross-line result), markers.csv (each ViewPoint field marker and the
zone near it, if any) and summary.md. With --labels (one or more labels.jsonl from the review app) it
also counts how many proposed zones reviewers accepted or rejected, per type and level.

Same processing as the app (run_pipeline → analysis track → analyze_line), then the GPS cross-line
check over all lines of the same channel. Nothing here is a calibrated probability.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path

from gpr_analyzer.app import geo
from gpr_analyzer.detect import analyze_line, to_report_records
from gpr_analyzer.detect import crossline
from gpr_analyzer.detect.evaluate import line_counts, marker_hits, summary_markdown, tally_labels
from gpr_analyzer.io.impulseradar import read_impulseradar
from gpr_analyzer.processing.auto import run_pipeline
from gpr_analyzer.report.schema import Zone


def _write(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("survey", type=Path)
    ap.add_argument("--out", type=Path, default=Path("eval_out"))
    ap.add_argument("--labels", type=Path, nargs="*", default=[])
    ap.add_argument("--channel", choices=("0", "1", "all"), default="all", help="_0 = high, _1 = low frequency")
    ap.add_argument("--limit", type=int, default=0, help="only the first N lines (quick look)")
    ap.add_argument("--gps-uncertainty", type=float, default=crossline.CrossLineParams().gps_uncertainty_m,
                    help="horizontal GPS error in m (RTK ~0.05, plain GNSS 2-5)")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in a.survey.glob("*.iprh") if p.with_suffix(".iprb").exists()
                   and p.with_suffix(".iprb").stat().st_size > 0)
    if a.channel != "all":
        files = [p for p in files if p.stem.endswith(f"_{a.channel}")]
    if a.limit:
        files = files[:a.limit]
    if not files:
        print(f"no ImpulseRadar lines in {a.survey}", file=sys.stderr)
        return 1

    line_rows, zone_rows, mark_rows, tracks, origin = [], [], [], [], None
    for i, path in enumerate(files, 1):
        t0 = time.monotonic()
        try:
            (rg,) = read_impulseradar(path)
            res = run_pipeline(rg, tracks={"analysis"})
            det = analyze_line(res.analysis, velocity_m_per_ns=res.velocity_m_per_ns,
                               velocity_source=res.velocity_source_code)
        except Exception as ex:  # noqa: BLE001 - one bad line must not stop the survey
            print(f"[{i}/{len(files)}] {path.name}: FAILED {type(ex).__name__}: {ex}", file=sys.stderr)
            line_rows.append({"line": path.stem, "error": f"{type(ex).__name__}: {ex}"})
            continue
        zones, regions = to_report_records(det, line_id=path.stem)
        an = res.analysis
        length = an.n_traces * an.dx_m if an.dx_m else None
        # ViewPoint names a channel's file <project>_<line>_<ch>; both channels share the line's track
        base, _, ch = path.stem.rpartition("_")
        if not (base and ch.isdigit()):
            base, ch = path.stem, ""
        row = {"line": path.stem, "channel": ch, "traces": an.n_traces,
               "length_m": round(length, 2) if length else None, "velocity_m_per_ns": round(res.velocity_m_per_ns, 4),
               "velocity_source": res.velocity_source_code, "hyperbolas": len(det.hyperbolas),
               **line_counts(zones, regions, length), "seconds": round(time.monotonic() - t0, 1)}
        line_rows.append(row)
        marks = rg.metadata.get("markers") or []
        if marks:
            for m in marker_hits(marks, zones, an.dt_ns, 0 if not rg.metadata.get("zero_level_sample") else
                                 int(rg.metadata["zero_level_sample"]), an.dx_m):
                mark_rows.append({"line": path.stem, **m})
        pos = geo.trace_positions(rg.metadata.get("gps"), an.n_traces)
        xy = None
        if pos is not None:
            origin = origin or (float(pos[0][0]), float(pos[1][0]))
            xy = crossline.local_xy(pos[0], pos[1], *origin)
        line_id = path.stem
        rz = [Zone.from_dict(z) for z in zones]
        tracks.append(crossline.LineTrack(
            line_id=line_id, source=base, channel=ch, xy=xy, zones=rz,
            dx_m=an.dx_m, gps_ok=xy is not None and crossline.gps_length_ok(xy, length, 0.15),
            rtk_fixed=crossline.all_rtk_fixed(rg.metadata.get("gps"))))
        for z in zones:
            zone_rows.append({"line": line_id, "zone": z["zone_id"], "type": z["suspected_type"],
                              "level": z["confidence"], "trace_start": z["trace_start"], "trace_end": z["trace_end"],
                              "distance_start_m": z["distance_start_m"], "distance_end_m": z["distance_end_m"],
                              "time_top_ns": round(z["time_top_ns"], 2), "time_bottom_ns": round(z["time_bottom_ns"], 2),
                              "reasons": " | ".join(e["params"].get("text_en", "") for e in z["evidence"])})
        print(f"[{i}/{len(files)}] {path.name}: {row['suspicious_medium_plus']} suspicious (medium+), "
              f"{len(zones)} zones, v={row['velocity_m_per_ns']} ({row['velocity_source']}), {row['seconds']} s")

    cross = crossline.check_lines(tracks, crossline.CrossLineParams(gps_uncertainty_m=a.gps_uncertainty))
    for r in zone_rows:
        c = cross.get((r["line"], r["zone"]))
        if c is not None:
            r["cross_line"] = c.status
            r["cross_line_level"] = c.new_confidence or ""
    labels = tally_labels(a.labels) if a.labels else None
    ok = [r for r in line_rows if "error" not in r]
    _write(a.out / "lines.csv", line_rows)
    _write(a.out / "zones.csv", zone_rows)
    _write(a.out / "markers.csv", mark_rows)
    md = summary_markdown(ok, mark_rows, labels, Counter(c.status for c in cross.values()))
    (a.out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
