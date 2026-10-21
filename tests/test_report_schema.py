import json

import pytest

from gpr_analyzer.report.rules import find_wording_violations, suggest_action
from gpr_analyzer.report.schema import Evidence, SurveyResult, Zone


def zone(**kw):
    d = dict(zone_id="A", line_id="L1", trace_start=10, trace_end=20, time_top_ns=40.0, time_bottom_ns=60.0,
             velocity_m_per_ns=0.1, velocity_source="default", suspected_type="void_like", confidence="medium")
    d.update(kw)
    return Zone(**d)


def test_depth_is_a_range_from_two_way_time():
    z = zone(velocity_uncertainty_m_per_ns=0.01)
    assert z.depth_top_m == pytest.approx(2.0)
    assert z.depth_bottom_m == pytest.approx(3.0)
    lo, hi = z.depth_range_with_uncertainty()
    assert lo == pytest.approx(1.8) and hi == pytest.approx(3.3)


def test_validation():
    with pytest.raises(ValueError):
        zone(confidence="87%")
    with pytest.raises(ValueError):
        zone(suspected_type="cave")
    with pytest.raises(ValueError):
        zone(trace_end=5)
    with pytest.raises(ValueError, match="ground_truth"):
        zone(confidence="confirmed")
    assert zone(confidence="confirmed", ground_truth={"method": "drill", "result": "void 0.6 m"})
    with pytest.raises(ValueError):
        Evidence("maybe", "x")


def test_json_round_trip_keeps_everything_but_the_data():
    from gpr_analyzer.report.demo import mock_survey

    s = mock_survey(with_radargram=False)
    text = s.to_json()
    d = json.loads(text)
    assert d["schema_version"]
    assert d["lines"][0]["zones"][0]["depth_top_m"] == pytest.approx(s.zones[0].depth_top_m, abs=1e-3)
    s2 = SurveyResult.from_json(text)
    assert s2.to_dict() == s.to_dict()
    assert isinstance(s2.zones[0].evidence[0], Evidence)
    assert "ถนน" in text  # Thai is kept readable, not \u-escaped


def test_zone_accepts_dicts_from_detect():
    z = Zone.from_dict({**zone().to_dict(), "unknown_key": 1,
                        "evidence": [{"kind": "present", "code": "strong_reflection"}]})
    assert z.evidence[0].code == "strong_reflection"


@pytest.mark.parametrize("conf,surface,expected", [
    ("high", "road", "1"), ("high", "open", "2"), ("medium", "building", "2"), ("medium", "open", "3"),
    ("low", "road", "3"),
])
def test_suggest_action_void_like(conf, surface, expected):
    assert suggest_action(zone(confidence=conf), surface) == expected


def test_suggest_action_other_types_and_overrides():
    assert suggest_action(zone(confidence="confirmed", ground_truth={"m": "drill"}), "road") == "E"
    assert suggest_action(zone(suspected_type="artifact", confidence="high"), "road") == "none"
    assert suggest_action(zone(suspected_type="pipe_point", confidence="high"), "road") == "none"
    assert suggest_action(zone(suspected_type="scattered", confidence="low"), "road") == "none"
    assert suggest_action(zone(review="rejected", confidence="high"), "road") == "none"
    assert suggest_action(zone(action="E"), "open") == "E"


@pytest.mark.parametrize("text", ["Void found at 5 m", "cavity detected", "พบโพรงที่ความลึก 5 ม.",
                                  "confidence 85%", "ความเชื่อมั่น 70 %"])
def test_wording_rules_catch(text):
    assert find_wording_violations(text)


@pytest.mark.parametrize("text", ["Void-like zone", "โซนคล้ายโพรง", "Red = high amplitude, not a void by itself."])
def test_wording_rules_allow(text):
    assert find_wording_violations(text) == []


@pytest.mark.parametrize("name", ["center_frequency", "dewow", "time_zero", "bandpass", "background_removal",
                                  "gain_sec", "velocity", "envelope", "agc", "migration", "artifact_check"])
def test_processing_step_names_are_translated(name):
    from gpr_analyzer.report.i18n import Translator

    assert Translator("th").has(f"step_{name}")
