import pytest
from detect_synth import Line

from gpr_analyzer.detect import detect_artifacts, find_zones


def boxes(zones):
    return [(z.trace_start, z.trace_end, z.time_start_ns, z.time_end_ns) for z in zones]


def contains(z, x, t):
    return z.trace_start <= x <= z.trace_end and z.time_start_ns <= t <= z.time_end_ns


def test_point_target_zone_with_geometry():
    r = Line(v=0.1, dx_m=0.05).direct_wave().point(150, 40, amp=2.0).noise(0.03).radargram()
    zones = find_zones(r)
    assert len(zones) == 1
    z = zones[0]
    assert contains(z, 150, 40.5)
    # depth = v * t / 2
    assert z.depth_top_m == pytest.approx(0.1 * z.time_start_ns / 2)
    assert z.depth_bottom_m == pytest.approx(0.1 * z.time_end_ns / 2)
    assert z.width_m == pytest.approx((z.trace_end - z.trace_start + 1) * 0.05)
    assert z.distance_start_m == pytest.approx(z.trace_start * 0.05)
    assert z.id == "A"


def test_quiet_line_has_no_zones():
    assert find_zones(Line().direct_wave().noise(0.05).radargram()) == []


def test_separate_targets_get_separate_zones_in_trace_order():
    r = (Line().direct_wave().point(60, 30, 2).void(180, 230, 50, 65)
         .noise(0.03).radargram())
    zones = find_zones(r)
    assert [z.id for z in zones] == ["A", "B"]
    assert contains(zones[0], 60, 30.5)
    assert contains(zones[1], 205, 50.5)


def test_stripe_does_not_become_a_zone_but_is_noted():
    line = Line().direct_wave().void(100, 150, 45, 60).stripe(160, 168).noise(0.03)
    r = line.radargram()
    arts = detect_artifacts(r)
    zones = find_zones(r, artifacts=arts)
    record = r.n_samples * r.dt_ns
    for z in zones:
        assert not (z.trace_start >= 158 and z.trace_end <= 170)  # nothing made of the stripe alone
        assert z.time_end_ns - z.time_start_ns < 0.6 * record
    main = [z for z in zones if contains(z, 125, 45.5)]
    assert len(main) == 1 and main[0].trace_end < 160
    stripes = {a.id for a in arts if a.kind == "vertical_stripe"}
    assert stripes & set(main[0].nearby_artifacts)


def test_velocity_override_changes_depth_only():
    r = Line().direct_wave().point(150, 40, 2).noise(0.03).radargram()
    z1 = find_zones(r)[0]
    z2 = find_zones(r, velocity_m_per_ns=0.135)[0]
    assert z1.time_start_ns == z2.time_start_ns
    assert z2.depth_top_m == pytest.approx(z1.depth_top_m * 1.35)
    assert z2.velocity_m_per_ns == 0.135
