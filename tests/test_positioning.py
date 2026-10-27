import pytest

from gpr_analyzer.app.positioning import positioning_qa


def rows(traces, xs):
    return [dict(trace=t, lat=13.0, lon=100 + x / 108000, date="2026-10-24", time=f"09:00:{i:02d}")
            for i, (t, x) in enumerate(zip(traces, xs))]


def test_clean_and_gap_with_reversal():
    clean = positioning_qa(rows(range(0, 101, 10), range(11)), 101, .1)
    bad = positioning_qa(rows([0, 10, 90, 100], [0, 1, 9, 8]), 101, .1)
    assert not clean["low_density"] and not clean["sharp_turns"]
    assert bad["gps_points_per_m"] < clean["gps_points_per_m"]
    assert bad["low_density"] and bad["max_wheel_gap_m"] == 8
    assert bad["sharp_turns"][0]["angle_deg"] == pytest.approx(180)
    assert bad["traces_per_m"] == 10
    assert bad["segments"][1]["gps_points_per_m"] == .125
    assert bad["start_time"] == "2026-10-24 09:00:00"
    assert bad["end_time"] == "2026-10-24 09:00:03"


def test_unknown_spacing_and_invalid_fixes():
    f = rows([0, 10], [0, 1])
    f += [dict(f[0]), dict(f[1], trace=20, quality=0), dict(f[1], trace=30, lat=float("nan"))]
    qa = positioning_qa(f, 40, None)
    assert qa["usable_fixes"] == 2
    assert qa["gps_points_per_m"] is None and qa["traces_per_m"] is None
    assert not qa["low_density"]
