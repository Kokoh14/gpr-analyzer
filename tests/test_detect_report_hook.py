from detect_synth import Line

from gpr_analyzer.detect import detect_zones

REPORT_TYPES = {"void_like", "pipe_point", "layer", "scattered", "artifact", "unknown"}


def test_detect_zones_matches_report_layout():
    line = (Line().direct_wave().void(110, 170, 40, 60, amp=2.0).pull_up(85, 110, 170, 4.0)
            .point(240, 30, 2).stripe(200, 206).noise(0.03))
    zones, regions = detect_zones(line.radargram(), line_id="L1")
    assert zones and regions
    for z in zones:
        assert z["line_id"] == "L1"
        assert z["suspected_type"] in REPORT_TYPES
        assert z["confidence"] in ("high", "medium", "low")
        assert z["action"] in (None, "E", "1", "2", "3")
        assert z["time_bottom_ns"] >= z["time_top_ns"]
        assert all(e["kind"] in ("present", "absent", "warning") for e in z["evidence"])
    void = [z for z in zones if z["suspected_type"] == "void_like"]
    assert void and void[0]["action"] is not None
    assert {r["reason"] for r in regions} >= {"vertical_stripe"}


def test_records_load_into_report_schema_when_available():
    import pytest
    schema = pytest.importorskip("gpr_analyzer.report.schema")
    zones, regions = detect_zones(Line().direct_wave().point(150, 40, 2).noise(0.03).radargram(), line_id="L1")
    for z in zones:
        schema.Zone.from_dict(z)
    for a in regions:
        schema.ArtifactRegion(**a)
