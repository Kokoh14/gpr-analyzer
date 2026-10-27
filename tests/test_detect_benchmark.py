"""Regression guard on the synthetic clutter benchmark (scripts/detect_benchmark.py).

Numbers measured when the rules were set (12 lines: 9 of 14 targets located, 1 medium/high zone on a
target-free line matching nothing). These are synthetic; they protect against regressions, they do not
say how the program does on real ground.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import detect_benchmark  # noqa: E402


def test_clutter_benchmark_does_not_regress():
    rows, s = detect_benchmark.evaluate(n=6, seed=1)
    assert s["targets_found_any_level"] >= 4, s  # 4 of 8 when set; 0 before the plateau fix
    assert s["false_alarms_medium_plus_on_empty_lines"] <= 1, s
    for lvl in ("high", "medium"):
        assert s[lvl]["none"] <= 2, s  # medium/high zones that are on nothing at all
    # zones that hold an object but are much wider than it (the old failure was one box over the line)
    assert s["high"]["too_wide"] + s["medium"]["too_wide"] <= 16, s
