import numpy as np

from gpr_analyzer.detect.crossline import CrossLineParams, LineTrack, check_lines, gps_length_ok, local_xy
from gpr_analyzer.report.schema import Zone

DX = 0.02


def track(y: float, n: int = 1000, x0: float = 0.0) -> np.ndarray:
    """A straight east-going line at north offset y (m), one trace every DX."""
    return np.column_stack([x0 + np.arange(n) * DX, np.full(n, y)])


def zone(zid, line, x_from_m, x_to_m, top_m, bottom_m, typ="void_like", conf="medium", v=0.1):
    return Zone(zone_id=zid, line_id=line, trace_start=int(x_from_m / DX), trace_end=int(x_to_m / DX),
                time_top_ns=2 * top_m / v, time_bottom_ns=2 * bottom_m / v, velocity_m_per_ns=v,
                velocity_source="hyperbola_fit", suspected_type=typ, confidence=conf)


def run(a_zones, b_zones, gap=0.5, params=None, b_source="B.iprb", **kw):
    a = LineTrack("A", "A.iprb", 1, track(0.0), a_zones, DX)
    b = LineTrack("B", b_source, 1, track(gap), b_zones, DX, **kw)
    return check_lines([a, b], params)


def test_wide_void_missing_on_neighbour_is_lowered():
    res = run([zone("A1", "A", 8, 12, 1.0, 1.5)], [], gap=0.5)
    r = res[("A", "A1")]
    assert r.status == "contradicted" and r.new_confidence == "low" and r.kind == "warning"
    assert "B" in r.text_th and "%" not in r.text_th


def test_void_seen_on_neighbour_is_confirmed_not_upgraded():
    res = run([zone("A1", "A", 8, 12, 1.0, 1.5)], [zone("B1", "B", 9, 11, 1.1, 1.6, typ="scattered")])
    r = res[("A", "A1")]
    assert r.status == "confirmed" and r.new_confidence is None and r.kind == "present"
    assert r.confirmed_by[0][:2] == ("B", "B1")
    assert res[("B", "B1")].status == "confirmed"


def test_depth_must_overlap():
    res = run([zone("A1", "A", 8, 12, 1.0, 1.5)], [zone("B1", "B", 9, 11, 3.0, 3.4)])
    assert res[("A", "A1")].status == "contradicted"


def test_narrow_zone_or_far_neighbour_is_not_judged():
    # 0.6 m wide zone, neighbour 0.5 m away + 0.5 m GPS error: the neighbour may simply pass beside it
    res = run([zone("A1", "A", 10, 10.6, 1.0, 1.3)], [], gap=0.5)
    assert res[("A", "A1")].status == "not_assessed" and res[("A", "A1")].new_confidence is None
    res = run([zone("A1", "A", 8, 12, 1.0, 1.5)], [], gap=3.0)
    assert res[("A", "A1")].status == "not_assessed"


def test_precise_gps_allows_closer_judgement():
    p = CrossLineParams(gps_uncertainty_m=0.05)
    res = run([zone("A1", "A", 10, 11.2, 1.0, 1.3)], [], gap=0.2, params=p)
    assert res[("A", "A1")].status == "contradicted"


def test_point_target_level_never_changes():
    res = run([zone("A1", "A", 10, 10.4, 0.8, 0.9, typ="pipe_point", conf="high")], [], gap=0.1,
              params=CrossLineParams(gps_uncertainty_m=0.0))
    r = res[("A", "A1")]
    assert r.status == "isolated" and r.new_confidence is None and "หิน" in r.text_th
    res = run([zone("A1", "A", 10, 10.4, 0.8, 0.9, typ="pipe_point", conf="high")],
              [zone("B1", "B", 10.1, 10.5, 0.85, 0.95, typ="pipe_point")], gap=0.8)
    assert res[("A", "A1")].status == "confirmed" and "ท่อ" in res[("A", "A1")].text_th


def test_layers_artifacts_other_channel_and_same_file_are_ignored():
    res = run([zone("A1", "A", 8, 12, 1.0, 1.5, typ="layer")], [])
    assert ("A", "A1") not in res
    # the other channel of the same file runs on the same track: it is not a neighbour
    res = run([zone("A1", "A", 8, 12, 1.0, 1.5)], [], gap=0.0, b_source="A.iprb")
    assert res[("A", "A1")].status == "not_assessed"


def test_excluded_section_on_neighbour_proves_nothing():
    res = run([zone("A1", "A", 8, 12, 1.0, 1.5)], [], excluded=[(300, 700)])
    assert res[("A", "A1")].status == "not_assessed"


def test_bad_or_missing_gps():
    a = LineTrack("A", "A.iprb", 1, None, [zone("A1", "A", 8, 12, 1.0, 1.5)], DX)
    b = LineTrack("B", "B.iprb", 1, track(0.5), [], DX)
    assert check_lines([a, b])[("A", "A1")].status == "not_assessed"
    xy = track(0.0)
    assert gps_length_ok(xy, len(xy) * DX, 0.15)
    assert not gps_length_ok(xy * 1.5, len(xy) * DX, 0.15)


def test_local_xy_metres():
    xy = local_xy(np.array([13.75, 13.75 + 1e-5]), np.array([100.5, 100.5]), 13.75, 100.5)
    assert np.allclose(xy[1], [0.0, 1.11], atol=0.01)
