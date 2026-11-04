from detect_synth import Line

from gpr_analyzer.detect import (
    detect_abnormal_traces,
    detect_artifacts,
    detect_bottom_noise,
    detect_vertical_stripes,
)


def base(seed=0):
    return Line(seed=seed).direct_wave().layer(30, amp=1.0).point(150, 45, amp=1.5).noise(0.05)


def test_full_depth_stripe_is_flagged():
    r = base().stripe(200, 212).radargram()
    flags = detect_vertical_stripes(r)
    assert len(flags) == 1
    f = flags[0]
    assert f.kind == "vertical_stripe"
    assert 198 <= f.trace_start <= 201 and 211 <= f.trace_end <= 214


def test_two_separate_stripes():
    r = base().stripe(60, 64).stripe(240, 255).radargram()
    flags = detect_vertical_stripes(r)
    assert [(f.trace_start // 10, f.trace_end // 10) for f in flags] == [(6, 6), (24, 25)]


def test_localised_strong_reflector_is_not_a_stripe():
    # strong but only over a few ns: a real target, not a full-depth band
    r = base().void(100, 120, 40, 55, amp=4.0).radargram()
    assert detect_vertical_stripes(r) == []


def test_clean_line_has_no_artifacts():
    flags = detect_artifacts(base().radargram())
    assert flags == []


def test_dead_and_stationary_traces():
    line = base()
    line.dead(50)
    line.stationary(120, 126)
    flags = detect_abnormal_traces(line.radargram())
    kinds = {(f.kind, f.trace_start, f.trace_end) for f in flags}
    assert ("dead_trace", 50, 50) in kinds
    stat = [f for f in flags if f.kind == "stationary_traces"]
    assert len(stat) == 1 and stat[0].trace_start <= 120 and stat[0].trace_end >= 125


def test_gained_bottom_noise_onset():
    r = base().gained_bottom_noise(from_ns=75, sigma=0.6).radargram()
    flags = detect_bottom_noise(r)
    assert len(flags) == 1
    f = flags[0]
    assert f.kind == "bottom_noise"
    assert 68 <= f.time_start_ns <= 80
    assert f.trace_start == 0 and f.trace_end == r.n_traces - 1


def test_artifact_flags_have_ids_and_bilingual_reason():
    r = base().stripe(200, 212).dead(20).radargram()
    flags = detect_artifacts(r)
    assert len({f.id for f in flags}) == len(flags)
    for f in flags:
        assert f.reason_th and f.reason_en


def test_tall_wide_target_edges_are_not_stripes():
    r = base().void(100, 160, 20, 85, amp=3.0, multiples=6).radargram()
    assert detect_vertical_stripes(r) == []


def railway_line():
    """0.36 m traces like the IDS railway survey; a 14 m crossing whose direct wave rings down the record."""
    ln = Line(n_traces=600, dx_m=0.36).direct_wave().layer(30, amp=1.0).noise(0.05).stripe(100, 102)
    for x in range(300, 340):
        for k in range(12):
            ln._put(x, 3.0 + 7.0 * k, 30.0 * 0.8 ** k * (-1) ** k)
    return ln.radargram()


def test_surface_change_is_flagged_and_masked():
    from gpr_analyzer.detect import analyze_line
    from gpr_analyzer.detect.artifacts import detect_surface_changes

    r = railway_line()
    (f,) = detect_surface_changes(r)
    assert f.kind == "surface_change" and f.id == "SC1"
    assert 285 <= f.trace_start <= 300 and 339 <= f.trace_end <= 355  # the narrow stripe at 100 is not one
    det = analyze_line(r)
    assert any(a.kind == "surface_change" for a in det.artifacts)
    assert not [z for z in det.zones if f.trace_start <= (z.trace_start + z.trace_end) / 2 <= f.trace_end]


def test_no_surface_change_without_trace_spacing():
    from gpr_analyzer.detect.artifacts import detect_surface_changes

    r = railway_line()
    r.dx_m = None
    assert detect_surface_changes(r) == []
