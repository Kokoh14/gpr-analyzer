import pytest

from gpr_analyzer.detect.models import Confidence
from gpr_analyzer.detect.risk import (
    RiskContext,
    assess_collapse_risk,
    geometric_hazard,
    grade_from_score,
)


def test_grade_matrix_bands():
    assert [grade_from_score(s) for s in (16, 12, 9, 8, 6, 4, 3, 1)] == [1, 1, 2, 2, 3, 3, 4, 4]


@pytest.mark.parametrize("cover,width,mat,h", [
    (0.3, 1.0, "sand", 4),   # ratio 0.3 → H4
    (0.9, 1.0, "sand", 3),
    (1.8, 1.0, "sand", 2),
    (3.0, 1.0, "sand", 1),
    (0.25, 0.5, "clay", 4),  # cover ≤ 0.3 m forces H4 in soil
    (3.0, 2.0, "clay", 3),   # width ≥ 1.5 m lifts to at least H3
    (2.0, 1.0, "clay", 1),
    (0.4, 1.0, "rock", 4),
    (1.5, 1.0, "rock", 2),
    (2.0, 1.0, "rock", 1),
])
def test_geometric_hazard(cover, width, mat, h):
    assert geometric_hazard(cover, width, mat)[0] == h


def test_shallow_urban_example_is_grade_I():
    ctx = RiskContext(material="sand", groundwater=True, old_pipes=True, heavy_traffic=True,
                      pavement="cracked", consequence=3, depth_uncertainty=0.15)
    r = assess_collapse_risk(cover_m=0.35, width_m=1.2, confidence=Confidence.MEDIUM, context=ctx)
    assert r.grade == 1 and r.grade_label_en.startswith("I ")
    assert r.likelihood == 4 and r.environment_modifier == 1 and r.n_environment_factors == 5


def test_deep_rock_example_is_grade_III():
    ctx = RiskContext(material="rock", consequence=2, depth_uncertainty=0.2)
    r = assess_collapse_risk(cover_m=5.2, width_m=8.0, confidence=Confidence.MEDIUM, context=ctx)
    assert (r.hazard, r.environment_modifier, r.likelihood, r.score, r.grade) == (3, -1, 2, 4, 3)
    assert r.cover_worst_m == pytest.approx(4.16)
    codes = {e.code for e in r.reasons if e.status == "warning"}
    assert "waltham_span" in codes  # cavity wider than rock roof is thick


def test_worst_case_cover_takes_the_worse_grade():
    ctx = RiskContext(material="sand", consequence=3, depth_uncertainty=0.2)
    # nominal ratio 1.1 → H2; worst cover 0.88 → ratio 0.8 → H3
    r = assess_collapse_risk(cover_m=1.1, width_m=1.0, confidence=Confidence.HIGH, context=ctx)
    assert r.hazard == 3 and r.grade_nominal > r.grade
    assert any(e.code == "worst_case_cover" for e in r.reasons)


def test_confidence_changes_action_not_grade():
    ctx = RiskContext(material="sand", consequence=4)
    hi = assess_collapse_risk(0.35, 1.2, Confidence.HIGH, ctx)
    lo = assess_collapse_risk(0.35, 1.2, Confidence.LOW, ctx)
    assert hi.grade == lo.grade
    assert hi.action_en != lo.action_en
    assert "verify" in lo.action_en.lower() or "confirm" in lo.action_en.lower()


def test_unknown_context_is_flagged_and_conservative():
    r = assess_collapse_risk(0.8, 1.0, Confidence.MEDIUM, RiskContext())
    warn = {e.code for e in r.reasons if e.status == "warning"}
    assert {"material_assumed", "consequence_assumed", "no_cross_line"} <= warn
    assert r.material == "sand"


def test_road_criteria_out_of_scope_when_deep():
    r = assess_collapse_risk(4.0, 2.0, Confidence.MEDIUM, RiskContext(material="clay", consequence=2))
    assert any(e.code == "shallow_criteria_out_of_scope" for e in r.reasons)


def test_no_width_means_no_grade():
    r = assess_collapse_risk(1.0, None, Confidence.MEDIUM, RiskContext(material="sand", consequence=2))
    assert r.grade is None and any(e.code == "no_width" for e in r.reasons)
