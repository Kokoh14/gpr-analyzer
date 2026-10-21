from detect_synth import Line

from gpr_analyzer.detect import analyze_line
from gpr_analyzer.detect.models import Confidence, ZoneType


def only_zone(r, **kw):
    res = analyze_line(r, **kw)
    assert len(res.zones) == 1, [(z.trace_start, z.trace_end, z.time_start_ns) for z in res.zones]
    return res.zones[0]


def test_point_target():
    z = only_zone(Line().direct_wave().point(150, 40, 2).noise(0.03).radargram())
    assert z.suspected_type == ZoneType.POINT
    assert z.confidence in (Confidence.HIGH, Confidence.MEDIUM)
    assert any(e.code == "hyperbola_apex" and e.status == "found" for e in z.evidence)


def test_layer():
    z = only_zone(Line().direct_wave().layer(40, amp=2.0, x0=20, x1=280).noise(0.03).radargram())
    assert z.suspected_type == ZoneType.LAYER
    assert z.confidence in (Confidence.HIGH, Confidence.MEDIUM)


def test_void_with_all_signs_is_high():
    line = (Line().direct_wave(amp=10.0).void(110, 170, 40, 60, amp=2.0, polarity=-1)
            .pull_up(85, 110, 170, shift_ns=4.0).noise(0.03))
    res = analyze_line(line.radargram())
    z = [z for z in res.zones if z.trace_start <= 140 <= z.trace_end and z.time_start_ns <= 41 <= z.time_end_ns][0]
    assert z.suspected_type == ZoneType.VOID_LIKE
    assert z.confidence == Confidence.HIGH, z.summary_th()
    found = {e.code for e in z.evidence if e.status == "found"}
    assert {"polarity_reversal", "edge_hyperbolas", "multiples", "pull_up"} <= found


def test_same_polarity_blob_is_not_void_like_medium_or_high():
    # strong + discontinuous only: the handoff rule says red alone is not a cavity
    line = Line().direct_wave(amp=10.0).void(110, 170, 40, 60, amp=2.0, polarity=+1, edges=False, multiples=0)
    res = analyze_line(line.noise(0.03).radargram())
    z = [z for z in res.zones if z.time_start_ns <= 40.5 <= z.time_end_ns][0]  # the roof
    void = [a for a in z.alternatives if a.type == ZoneType.VOID_LIKE]
    if z.suspected_type == ZoneType.VOID_LIKE:
        assert z.confidence == Confidence.LOW
    else:
        assert not void or void[0].confidence == Confidence.LOW
    absent = {e.code for e in z.evidence if e.status == "absent"} | {
        e.code for a in z.alternatives for e in a.evidence if e.status == "absent"}
    assert "polarity_reversal" in absent


def test_scattered_zone():
    line = Line(seed=3).direct_wave().scatter(100, 200, 35, 65, n=25).noise(0.03)
    res = analyze_line(line.radargram())
    assert res.zones
    big = max(res.zones, key=lambda z: z.trace_end - z.trace_start)
    assert big.suspected_type == ZoneType.SCATTERED


def test_zone_in_gained_noise_is_artifact():
    line = Line().direct_wave().point(150, 40, 2).noise(0.03).gained_bottom_noise(from_ns=75, sigma=0.8)
    res = analyze_line(line.radargram())
    target = [z for z in res.zones if z.time_start_ns < 60]
    assert len(target) == 1 and target[0].suspected_type == ZoneType.POINT
    for z in res.zones:
        if z.time_start_ns >= 75:
            assert z.suspected_type == ZoneType.ARTIFACT


def test_evidence_symbols_and_no_percentages():
    line = Line().direct_wave().void(110, 170, 40, 60, amp=2.0).stripe(180, 186).noise(0.03)
    res = analyze_line(line.radargram())
    z = res.zones[0]
    syms = {e.symbol for e in z.evidence}
    assert syms <= {"✓", "✗", "⚠", "–"}
    assert "⚠" in syms  # stripe right next to the zone
    assert "%" not in res.to_json()
