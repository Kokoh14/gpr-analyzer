"""Synthetic-only review benchmark. Run from repository root; optional output JSON path."""
import cProfile
import json
from pathlib import Path
import pstats
import statistics
import sys
import time
import tracemalloc
from types import SimpleNamespace

import numpy as np

from gpr_analyzer.app.scene3d import _texture
from gpr_analyzer.detect import analyze_line
from gpr_analyzer.processing.auto import run_pipeline
from gpr_analyzer.synthetic import make_scene


def retained_bytes(result):
    arrays = []
    for name in ("raw", "analysis", "layers", "energy", "envelope", "display", "viewpoint"):
        rg = getattr(result, name)
        if rg is not None and not any(np.shares_memory(rg.data, a) for a in arrays):
            arrays.append(rg.data)
    return sum(a.nbytes for a in arrays)


def main():
    raw, _ = make_scene()
    full = run_pipeline(raw)
    profiler = cProfile.Profile()
    profiler.enable()
    app = run_pipeline(raw, tracks={"analysis", "envelope", "display", "viewpoint"})
    analyze_line(app.analysis, velocity_m_per_ns=app.velocity_m_per_ns, velocity_source=app.velocity_source_code)
    profiler.disable()
    stats = pstats.Stats(profiler)
    d = np.random.default_rng(1).standard_normal((1100, 5000), dtype=np.float32)
    result = SimpleNamespace(analysis=SimpleNamespace(data=d))
    rows = np.linspace(0, 1099, 160).astype(int)
    cols = np.linspace(0, 4999, 192).astype(int)
    _texture(SimpleNamespace(result=result), rows, cols, "grey")  # warm imports
    cold, warm = [], []
    for _ in range(3):
        line = SimpleNamespace(result=result)
        start = time.perf_counter(); _texture(line, rows, cols, "grey"); cold.append(time.perf_counter()-start)
        start = time.perf_counter(); _texture(line, rows, cols, "grey"); warm.append(time.perf_counter()-start)
    tracemalloc.start()
    _texture(SimpleNamespace(result=result), rows, cols, "grey")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    data = {"data":"synthetic only", "hilbert_calls_processing_and_detect":sum(v[1] for k,v in stats.stats.items() if k[2]=="hilbert"),
            "retained_tracks_bytes":{"default_all_tracks":retained_bytes(full), "app_requested_tracks":retained_bytes(app)},
            "texture":{"source_bytes":d.nbytes, "cold_median_seconds":statistics.median(cold),
                       "cache_hit_median_seconds":statistics.median(warm), "cold_tracemalloc_peak_extra_bytes":peak}}
    encoded = json.dumps(data, indent=2)
    if len(sys.argv)>1:
        out = Path(sys.argv[1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(encoded, encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
