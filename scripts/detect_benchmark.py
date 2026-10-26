"""Benchmark the automatic detection on synthetic lines with known truth, through the app's own path
(run_pipeline → analysis track → analyze_line).

The lines are shaped like the real ImpulseRadar CrossOver 800 MHz channel (0.0186 m trace step, time
zero at the header ZERO LEVEL, v ≈ 0.1 m/ns) and carry what real lines carry besides targets: many weak
stones and roots (clutter), a dipping layer, antenna ringing, stripes from lift-off, a stop and gained
noise at the bottom. Half of the lines have no target at all, so every medium/high zone there is a false
alarm.

This measures behaviour on synthetic data only. It is a regression guard and a way to compare rule
changes, not an accuracy figure for real ground: that needs reviewed real lines (labels.jsonl) or
dug/drilled truth, see scripts/eval_detect_real.py.

    python scripts/detect_benchmark.py            # table on stdout
    python scripts/detect_benchmark.py --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field

import numpy as np

from gpr_analyzer.detect import analyze_line
from gpr_analyzer.processing.auto import run_pipeline
from gpr_analyzer.synthetic.generator import Cavity, Layer, PointTarget, make_scene

DX = 0.0186
V = 0.1
CLUTTER_MAX_AMP = 0.35  # point scatterers at or below this are clutter (stones), not targets
SUSPICIOUS = ("point_target", "void_like", "scattered", "unclassified")


@dataclass
class Scene:
    name: str
    targets: list[PointTarget] = field(default_factory=list)
    clutter: list[PointTarget] = field(default_factory=list)
    cavities: list[Cavity] = field(default_factory=list)
    stop: tuple[int, int] | None = None


def scenes(n: int = 12, seed: int = 1) -> list[Scene]:
    """n lines; odd ones have pipes / a cavity, even ones are targetless (clutter, layer, artifacts only)."""
    rng = np.random.default_rng(seed)
    out = []
    length = 1200 * DX
    for i in range(n):
        s = Scene(f"S{i:02d}")
        s.clutter = [PointTarget(float(rng.uniform(0.5, length - 0.5)), float(rng.uniform(0.15, 2.2)),
                                 float(rng.uniform(0.05, CLUTTER_MAX_AMP))) for _ in range(int(rng.integers(15, 35)))]
        if i % 2:
            for _ in range(int(rng.integers(1, 4))):
                s.targets.append(PointTarget(float(rng.uniform(1.5, length - 1.5)), float(rng.uniform(0.4, 1.8)),
                                             float(rng.uniform(0.8, 1.5))))
            if i % 4 == 1:
                x0 = float(rng.uniform(3, length - 6))
                top = float(rng.uniform(0.5, 1.2))
                s.cavities.append(Cavity(x0, x0 + float(rng.uniform(0.8, 2.0)), top, top + 0.4, amplitude=1.5))
        if i % 3 == 0:
            a = int(rng.integers(100, 1000))
            s.stop = (a, a + 25)
        out.append(s)
    return out


def radargram(s: Scene, seed: int):
    rg, truth = make_scene(
        n_traces=1200, n_samples=640, time_window_ns=64.0, dx_m=DX, velocity_m_per_ns=V, freq_mhz=800.0,
        targets=s.targets + s.clutter, layers=[Layer(depth_m=1.9, amplitude=0.3, dip=0.03)],
        cavities=s.cavities, time_zero_ns=6.8, time_zero_jitter_ns=0.03, direct_wave_amplitude=3.0,
        ringing_ns=9.0, ringing_amplitude=0.4, artifact_traces=[300, 301, 302, 760],
        attenuation_db_per_ns=0.6, noise_std=0.004, seed=seed)
    rg.metadata["zero_level_sample"] = 68
    if s.stop:
        a, b = s.stop
        rg.data[:, a:b] = rg.data[:, [a]] + 0.001 * np.random.default_rng(seed).standard_normal((rg.n_samples, b - a))
    return rg, truth


MAX_EXTRA_WIDTH_M = 2.0  # a zone more than this wider than the object it covers does not locate it


def _hits(z: dict, items: list[tuple[float, float, float]], tol_x: float, tol_z: float) -> bool:
    """items: (x0_m, x1_m, depth_m) footprints. The zone must overlap the object, contain its top depth and
    not be much wider than it."""
    return any(z["d0"] - tol_x <= x1 and x0 <= z["d1"] + tol_x and z["z0"] - tol_z <= d <= z["z1"] + tol_z
               and z["d1"] - z["d0"] <= x1 - x0 + MAX_EXTRA_WIDTH_M for x0, x1, d in items)


def evaluate(n: int = 12, seed: int = 1, params=None) -> tuple[list[dict], dict]:
    rows = []
    for i, s in enumerate(scenes(n, seed)):
        rg, _ = radargram(s, seed + i)
        res = run_pipeline(rg, tracks={"analysis"})
        det = analyze_line(res.analysis, params, velocity_m_per_ns=res.velocity_m_per_ns,
                           velocity_source=res.velocity_source_code)
        targets = [(t.x_m, t.x_m, t.depth_m) for t in s.targets] + \
                  [(c.x_start_m, c.x_end_m, c.depth_top_m) for c in s.cavities]
        clutter = [(t.x_m, t.x_m, t.depth_m) for t in s.clutter]
        found = set()
        for z in det.zones:
            if z.suspected_type.value not in SUSPICIOUS:
                continue
            d = {"d0": z.distance_start_m, "d1": z.distance_end_m, "z0": z.depth_top_m, "z1": z.depth_bottom_m}
            core = z.features.get("roof_core")
            if z.suspected_type.value == "void_like" and core:  # a void is located by its roof, without the tails
                d["d0"], d["d1"] = core[0] * DX, core[1] * DX
            hit = _hits(d, targets, 0.3, 0.3)
            if hit:
                found |= {k for k, t in enumerate(targets) if _hits(d, [t], 0.3, 0.3)}
            rows.append({"scene": s.name, "zone": z.id, "type": z.suspected_type.value,
                         "confidence": z.confidence.value, "dist_m": round((z.distance_start_m + z.distance_end_m) / 2, 2),
                         "depth_m": round(z.depth_top_m, 2),
                         "match": "target" if hit else ("clutter" if _hits(d, clutter, 0.15, 0.15) else "none"),
                         "n_targets": len(targets), "found": len(found)})
        rows.append({"scene": s.name, "zone": "-", "type": "-", "confidence": "-", "dist_m": "", "depth_m": "",
                     "match": "summary", "n_targets": len(targets), "found": len(found)})
    return rows, summarise(rows)


def summarise(rows: list[dict]) -> dict:
    out = {}
    zones = [r for r in rows if r["match"] != "summary"]
    for lvl in ("high", "medium", "low"):
        sel = [r for r in zones if r["confidence"] == lvl]
        out[lvl] = {"zones": len(sel), "target": sum(r["match"] == "target" for r in sel),
                    "clutter": sum(r["match"] == "clutter" for r in sel), "none": sum(r["match"] == "none" for r in sel)}
    med_plus = [r for r in zones if r["confidence"] in ("high", "medium")]
    out["precision_medium_plus"] = (sum(r["match"] == "target" for r in med_plus) / len(med_plus)) if med_plus else None
    summ = [r for r in rows if r["match"] == "summary"]
    out["targets"] = sum(r["n_targets"] for r in summ)
    out["targets_found_any_level"] = sum(r["found"] for r in summ)
    empty = {r["scene"] for r in summ if r["n_targets"] == 0}
    out["false_alarms_medium_plus_on_empty_lines"] = sum(
        r["scene"] in empty and r["match"] == "none" for r in med_plus)
    out["empty_lines"] = len(empty)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--csv")
    a = ap.parse_args(argv)
    rows, summ = evaluate(a.n, a.seed)
    for r in rows:
        if r["match"] != "summary":
            print(f"{r['scene']} {r['zone']:>3} {r['type']:<13} {r['confidence']:<6} x={r['dist_m']:>6} m "
                  f"z={r['depth_m']:>5} m  {r['match']}")
    for k, v in summ.items():
        print(f"{k}: {v}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
