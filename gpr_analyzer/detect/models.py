"""Data model for detection output: artifact flags, hyperbolas, zones, risk.

Everything here serialises to plain JSON so the report, CSV and GeoJSON writers can share one
source. Confidence is a level (high / medium / low) with ✓ / ✗ / ⚠ evidence, never a percentage:
the numbers would only mean something after calibration against dug or drilled ground truth.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any

SCHEMA = "gpr-analyzer/detection"
SCHEMA_VERSION = 1


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"high": 3, "medium": 2, "low": 1}[self.value]

    @property
    def label_th(self) -> str:
        return {"high": "สูง", "medium": "ปานกลาง", "low": "ต่ำ"}[self.value]


class ZoneType(str, Enum):
    POINT = "point_target"
    LAYER = "layer"
    VOID_LIKE = "void_like"
    SCATTERED = "scattered"
    ARTIFACT = "artifact"
    UNCLASSIFIED = "unclassified"

    @property
    def label_th(self) -> str:
        return ZONE_TYPE_LABELS[self][0]

    @property
    def label_en(self) -> str:
        return ZONE_TYPE_LABELS[self][1]


ZONE_TYPE_LABELS = {
    ZoneType.POINT: ("ท่อ / วัตถุจุด", "Pipe / point target"),
    ZoneType.LAYER: ("ชั้นดิน / หิน", "Soil / rock layer"),
    ZoneType.VOID_LIKE: ("โซนคล้ายโพรง", "Void-like zone"),
    ZoneType.SCATTERED: ("โซนกระจัดกระจาย", "Scattered zone"),
    ZoneType.ARTIFACT: ("Artifact", "Artifact"),
    ZoneType.UNCLASSIFIED: ("ยังจำแนกไม่ได้", "Unclassified"),
}

_SYMBOLS = {"found": "✓", "absent": "✗", "warning": "⚠", "not_assessed": "–"}


@dataclass
class Evidence:
    """One line of reasoning. status: found ✓ / absent ✗ / warning ⚠ / not_assessed –."""

    status: str
    code: str
    text_th: str
    text_en: str

    def __post_init__(self) -> None:
        if self.status not in _SYMBOLS:
            raise ValueError(f"unknown evidence status {self.status!r}")

    @property
    def symbol(self) -> str:
        return _SYMBOLS[self.status]

    def line(self, lang: str = "th") -> str:
        return f"{self.symbol} {self.text_th if lang == 'th' else self.text_en}"


@dataclass
class ArtifactFlag:
    id: str
    kind: str  # vertical_stripe | dead_trace | clipped_trace | stationary_traces | bottom_noise
    trace_start: int
    trace_end: int
    time_start_ns: float
    time_end_ns: float
    reason_th: str
    reason_en: str
    strength: float = 0.0  # detector-specific contrast; for ordering only

    def overlaps_traces(self, a: int, b: int, margin: int = 0) -> bool:
        return self.trace_start - margin <= b and a <= self.trace_end + margin


@dataclass
class Hyperbola:
    apex_trace: int
    apex_time_ns: float
    amplitude: float
    fit_r2: float
    n_picks: int
    half_width_traces: int
    velocity_m_per_ns: float | None  # None when dx_m is unknown
    apex_depth_m: float | None = None
    one_sided: bool = False  # only one limb found (line end or cavity edge)


@dataclass
class VelocityEstimate:
    velocity_m_per_ns: float
    spread_m_per_ns: float  # interquartile half-range of the individual fits
    n_fits: int
    source: str = "hyperbola_fit"


@dataclass
class TypeAssessment:
    type: ZoneType
    confidence: Confidence
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class RiskAssessment:
    """Collapse-risk grade (I emergency … IV monitor). Separate axis from detection confidence."""

    grade: int | None
    grade_label_th: str
    grade_label_en: str
    hazard: int | None
    environment_modifier: int
    n_environment_factors: int
    likelihood: int | None
    consequence: int
    score: int | None
    grade_nominal: int | None
    cover_m: float
    cover_worst_m: float
    width_m: float | None
    cover_width_ratio: float | None
    material: str
    confidence: Confidence
    action_th: str
    action_en: str
    reasons: list[Evidence] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Zone:
    id: str
    trace_start: int
    trace_end: int
    sample_start: int
    sample_end: int
    time_start_ns: float
    time_end_ns: float
    depth_top_m: float
    depth_bottom_m: float
    velocity_m_per_ns: float
    velocity_source: str
    distance_start_m: float | None
    distance_end_m: float | None
    width_m: float | None
    thickness_m: float
    peak_contrast: float
    suspected_type: ZoneType = ZoneType.UNCLASSIFIED
    confidence: Confidence = Confidence.LOW
    evidence: list[Evidence] = field(default_factory=list)
    alternatives: list[TypeAssessment] = field(default_factory=list)
    nearby_artifacts: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    risk: RiskAssessment | None = None
    review_status: str = "unreviewed"  # unreviewed | accepted | edited | rejected
    ground_truth: dict[str, Any] | None = None  # filled after dig/drill, for later calibration

    @property
    def n_traces(self) -> int:
        return self.trace_end - self.trace_start + 1

    def summary_th(self) -> str:
        """One-line summary in the handoff style."""
        ev = " ".join(e.line("th") for e in self.evidence)
        return (f"โซน {self.id} trace {self.trace_start}–{self.trace_end}, ลึก "
                f"{self.depth_top_m:.1f}–{self.depth_bottom_m:.1f} ม. | "
                f"{self.suspected_type.label_th}: {self.confidence.label_th} {ev}")


@dataclass
class DetectionResult:
    n_traces: int
    n_samples: int
    dt_ns: float
    dx_m: float | None
    parameters: dict[str, Any]
    artifacts: list[ArtifactFlag] = field(default_factory=list)
    hyperbolas: list[Hyperbola] = field(default_factory=list)
    velocity: VelocityEstimate | None = None
    zones: list[Zone] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _plain(asdict(self))
        return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, **d}

    def to_json(self, **kw: Any) -> str:
        kw.setdefault("ensure_ascii", False)
        kw.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DetectionResult":
        if d.get("schema") != SCHEMA:
            raise ValueError("not a detection result")
        d = {k: v for k, v in d.items() if k not in ("schema", "schema_version")}
        return cls(
            n_traces=d["n_traces"], n_samples=d["n_samples"], dt_ns=d["dt_ns"], dx_m=d["dx_m"],
            parameters=d["parameters"],
            artifacts=[ArtifactFlag(**a) for a in d["artifacts"]],
            hyperbolas=[Hyperbola(**h) for h in d["hyperbolas"]],
            velocity=VelocityEstimate(**d["velocity"]) if d["velocity"] else None,
            zones=[_zone_from_dict(z) for z in d["zones"]],
            source=d["source"],
        )

    @classmethod
    def from_json(cls, s: str) -> "DetectionResult":
        return cls.from_dict(json.loads(s))


def _plain(x: Any) -> Any:
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if hasattr(x, "item") and callable(x.item):  # numpy scalar
        return x.item()
    return x


def _evidence(items: list[dict]) -> list[Evidence]:
    return [Evidence(**e) for e in items]


def _zone_from_dict(z: dict[str, Any]) -> Zone:
    z = dict(z)
    z["suspected_type"] = ZoneType(z["suspected_type"])
    z["confidence"] = Confidence(z["confidence"])
    z["evidence"] = _evidence(z["evidence"])
    z["alternatives"] = [TypeAssessment(ZoneType(a["type"]), Confidence(a["confidence"]), _evidence(a["evidence"]))
                         for a in z["alternatives"]]
    if z.get("risk"):
        r = dict(z["risk"])
        r["confidence"] = Confidence(r["confidence"])
        r["reasons"] = _evidence(r["reasons"])
        z["risk"] = RiskAssessment(**r)
    known = {f.name for f in fields(Zone)}
    return Zone(**{k: v for k, v in z.items() if k in known})
