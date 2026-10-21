import pytest
from detect_synth import Line

from gpr_analyzer.detect import estimate_velocity, find_hyperbolas


def test_single_point_target_gives_apex_and_velocity():
    r = Line(v=0.1).direct_wave().point(150, 40, amp=2.0).noise(0.03).radargram()
    hs = find_hyperbolas(r)
    assert len(hs) >= 1
    h = max(hs, key=lambda h: h.amplitude)
    assert abs(h.apex_trace - 150) <= 2
    assert abs(h.apex_time_ns - 40) <= 1.5
    assert h.velocity_m_per_ns == pytest.approx(0.1, rel=0.12)
    assert h.fit_r2 >= 0.9


def test_velocity_from_several_targets():
    r = (Line(v=0.12).direct_wave().point(60, 30, 2).point(150, 50, 2).point(240, 40, 2)
         .noise(0.03).radargram())
    est = estimate_velocity(r)
    assert est is not None
    assert est.velocity_m_per_ns == pytest.approx(0.12, rel=0.1)
    assert est.n_fits >= 3


def test_flat_layer_is_not_a_hyperbola():
    r = Line().direct_wave().layer(40, amp=2.0).layer(60, amp=1.5).noise(0.03).radargram()
    assert find_hyperbolas(r) == []


def test_no_velocity_without_hyperbolas():
    r = Line().direct_wave().layer(40, amp=2.0).noise(0.03).radargram()
    assert estimate_velocity(r) is None
