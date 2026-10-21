"""Anomaly detection, artifact flags and rule-based scoring."""

from gpr_analyzer.detect.artifacts import (
    ArtifactParams,
    detect_abnormal_traces,
    detect_artifacts,
    detect_bottom_noise,
    detect_vertical_stripes,
)
from gpr_analyzer.detect.hyperbola import HyperbolaParams, estimate_velocity, find_hyperbolas
from gpr_analyzer.detect.zones import ZoneParams, find_zones
from gpr_analyzer.detect.classify import ClassifyParams, classify_zone
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
