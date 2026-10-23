"""Checks on a real ImpulseRadar CrossOver survey. Skipped unless GPR_REAL_DATA points at it.

The survey stays on the user's machine (it is not in this repository). Folder layout as ViewPoint
writes it: <project>_<line>_<ch>.iprh/.iprb/.mrk, <project>_<line>.cor, Export/AutoScreenShots/*.jpg.

    set GPR_REAL_DATA=G:\\...\\Sample Data   (Windows)    pytest tests/test_real_impulseradar.py
"""

import os
from pathlib import Path

import numpy as np
import pytest

from gpr_analyzer.io.impulseradar import read_impulseradar
from gpr_analyzer.processing import filters as F

ROOT = Path(os.environ.get("GPR_REAL_DATA", ""))
pytestmark = pytest.mark.skipif(not os.environ.get("GPR_REAL_DATA") or not ROOT.is_dir(),
                                reason="set GPR_REAL_DATA to a real ImpulseRadar survey folder")


def _lines():
    return sorted(p for p in ROOT.glob("*.iprh") if p.with_suffix(".iprb").stat().st_size > 0)


def test_every_line_reads_with_consistent_geometry():
    lines = _lines()
    assert lines
    for p in lines:
        (rg,) = read_impulseradar(p)
        h = rg.metadata["header"]
        assert rg.n_samples == int(h["SAMPLES"])
        assert rg.dt_ns * rg.n_samples == pytest.approx(float(h["TIMEWINDOW"]), rel=1e-3)
        # measured step: traces x DISTANCE INTERVAL lands on STOP POSITION
        assert rg.n_traces * rg.dx_m == pytest.approx(float(h["STOP POSITION"]), abs=2 * rg.dx_m)
        assert rg.metadata["zero_level_sample"] > 0
        if "gps" in rg.metadata:
            assert all(0 <= g["trace"] <= rg.n_traces + 1 for g in rg.metadata["gps"])


def test_empty_recordings_raise_clearly():
    for p in ROOT.glob("*.iprb"):
        if p.stat().st_size == 0:
            with pytest.raises(ValueError, match="no complete trace"):
                read_impulseradar(p)


def _pick_spread(p):
    (rg,) = read_impulseradar(p)
    live = np.ptp(rg.data, axis=0) > 0  # the first trace of most lines is all zero
    d = rg.data[:, live] - rg.data[:, live].mean(0)
    picks = F.first_break_picks(d, 0.3, 13)
    return np.percentile(picks, 95) - np.percentile(picks, 5)


def test_high_frequency_direct_wave_is_static():
    """800 MHz direct wave moves < 1 sample (0.1 ns) along a line, so one header shift is enough."""
    spreads = [_pick_spread(p) for p in _lines() if p.stem.endswith("_0")]
    assert max(spreads) < 1.0


def test_low_frequency_picks_stay_on_one_lobe():
    """400 MHz lobes are nearly equal; picking |amplitude| jumped ~7 samples between them."""
    spreads = [_pick_spread(p) for p in _lines() if p.stem.endswith("_1")]
    assert np.median(spreads) < 3.0 and max(spreads) < 6.0


def test_viewpoint_track_matches_screenshots_better_than_agc():
    pytest.importorskip("PIL")
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from fit_viewpoint_screens import score_folder

    if not (ROOT / "Export" / "AutoScreenShots").is_dir():
        pytest.skip("no ViewPoint screenshots in the folder")
    rows = [r for r in score_folder(ROOT) if r["registration_corr"] >= 0.3]
    assert rows
    for ch, floor in ((0, 0.65), (1, 0.55)):
        sub = [r for r in rows if r["channel"] == ch]
        vp = np.mean([r["viewpoint_row_corr"] for r in sub])
        agc = np.mean([r["display_row_corr"] for r in sub])
        assert vp > agc and vp >= floor
        assert np.mean([r["viewpoint_grey_rmse"] for r in sub]) < np.mean([r["display_grey_rmse"] for r in sub])
