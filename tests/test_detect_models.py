import json

from detect_synth import Line

from gpr_analyzer.detect import analyze_line
from gpr_analyzer.detect.models import DetectionResult, Evidence
from gpr_analyzer.detect.risk import RiskContext


def test_evidence_symbols():
    assert Evidence("found", "x", "", "").symbol == "✓"
    assert Evidence("absent", "x", "", "").symbol == "✗"
    assert Evidence("warning", "x", "", "").symbol == "⚠"
    assert Evidence("not_assessed", "x", "", "").symbol == "–"


def test_result_json_round_trip_with_risk():
    line = (Line().direct_wave().void(110, 170, 40, 60, amp=2.0).pull_up(85, 110, 170, 4.0)
            .stripe(220, 226).point(60, 30, 2).noise(0.03))
    res = analyze_line(line.radargram(), risk_context=RiskContext(material="clay", consequence=2))
    s = res.to_json()
    d = json.loads(s)
    assert d["schema"] == "gpr-analyzer/detection" and d["schema_version"] == 1
    assert d["parameters"]["velocity_m_per_ns"] > 0
    void = [z for z in d["zones"] if z["suspected_type"] == "void_like"]
    assert void and void[0]["risk"]["grade"] in (1, 2, 3, 4)
    assert all(z["ground_truth"] is None and z["review_status"] == "unreviewed" for z in d["zones"])
    back = DetectionResult.from_json(s)
    assert back.to_json() == s


def test_risk_only_for_void_like_zones():
    line = Line().direct_wave().point(60, 30, 2).noise(0.03)
    res = analyze_line(line.radargram(), risk_context=RiskContext(material="sand", consequence=2))
    assert all(z.risk is None for z in res.zones)
