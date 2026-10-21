"""Anomaly detection, artifact flags and rule-based scoring.

Main entry: ``analyze_line(radargram)`` → ``DetectionResult`` (JSON-serialisable).
``detect_zones(radargram, line_id=...)`` returns the same in the report schema layout.
See docs/detect.md.
"""

from gpr_analyzer.detect.artifacts import (
    ArtifactParams,
    detect_abnormal_traces,
    detect_artifacts,
    detect_bottom_noise,
    detect_vertical_stripes,
)
from gpr_analyzer.detect.classify import ClassifyParams, classify_zone
from gpr_analyzer.detect.hyperbola import HyperbolaParams, estimate_velocity, find_hyperbolas
from gpr_analyzer.detect.models import (
    ArtifactFlag,
    Confidence,
    DetectionResult,
    Evidence,
    Hyperbola,
    RiskAssessment,
    VelocityEstimate,
    Zone,
    ZoneType,
)
from gpr_analyzer.detect.pipeline import DetectParams, analyze_line, detect_zones, to_report_records
from gpr_analyzer.detect.risk import RiskContext, assess_collapse_risk
from gpr_analyzer.detect.zones import ZoneParams, find_zones

__all__ = [
    "ArtifactFlag", "ArtifactParams", "ClassifyParams", "Confidence", "DetectParams", "DetectionResult",
    "Evidence", "Hyperbola", "HyperbolaParams", "RiskAssessment", "RiskContext", "VelocityEstimate", "Zone",
    "ZoneParams", "ZoneType", "analyze_line", "assess_collapse_risk", "classify_zone", "detect_abnormal_traces",
    "detect_artifacts", "detect_bottom_noise", "detect_vertical_stripes", "detect_zones", "estimate_velocity",
    "find_hyperbolas", "find_zones", "to_report_records",
]
