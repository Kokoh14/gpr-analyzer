"""End-to-end on a line shaped like an early sample line (555 traces, ~145 ns, v ≈ 0.135 m/ns)."""

from detect_synth import Line

from gpr_analyzer.detect import analyze_line
from gpr_analyzer.detect.models import Confidence, ZoneType


def field_like():
    line = Line(n_samples=580, n_traces=555, dt_ns=0.25, dx_m=0.05, v=0.135, f_ghz=0.25, seed=7)
    line.direct_wave(amp=10.0).layer(35, amp=0.6)  # the ~35 ns band
    line.void(150, 300, 78, 108, amp=2.0, polarity=-1, multiples=3)
    for a, b in ((200, 202), (240, 242), (260, 262), (310, 312), (365, 386)):
        line.stripe(a, b, amp=1.5)
    line.noise(0.03).gained_bottom_noise(from_ns=120, sigma=0.8)
    return line.radargram()


def test_field_like_line():
    res = analyze_line(field_like())
    stripes = [(a.trace_start, a.trace_end) for a in res.artifacts if a.kind == "vertical_stripe"]
    for a, b in ((200, 202), (240, 242), (260, 262), (310, 312), (365, 386)):
        assert any(s <= b and a <= e for s, e in stripes), (a, b, stripes)
    noise = [a for a in res.artifacts if a.kind == "bottom_noise"]
    assert noise and 112 <= noise[0].time_start_ns <= 126

    deep = [z for z in res.zones if z.trace_start <= 225 <= z.trace_end and z.time_start_ns <= 79 <= z.time_end_ns]
    assert len(deep) == 1
    z = deep[0]
    # reversed roof + ringing inside, but no edge diffractions at this depth/frequency → medium, not high
    assert z.suspected_type == ZoneType.VOID_LIKE and z.confidence == Confidence.MEDIUM, z.summary_th()
    assert z.risk is not None and z.risk.grade in (1, 2, 3, 4)
    assert 4.5 <= z.depth_top_m <= 5.5  # 78 ns at 0.135 m/ns ≈ 5.3 m
    warn = [e for e in z.evidence if e.status == "warning"]
    assert any("365" in e.text_th for e in warn)  # the 365–385 band is called out next to the zone
    for z in res.zones:  # nothing in the gained noise is reported as a target
        if z.time_start_ns >= 126:
            assert z.suspected_type == ZoneType.ARTIFACT
    assert "%" not in res.to_json()
