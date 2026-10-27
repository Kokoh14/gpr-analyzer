"""Zone / survey records that feed every report output (PDF, Word, HTML, CSV, JSON).

One JSON-serialisable record per zone is the single source of truth. The detect/ package is
expected to emit `Zone` objects (or dicts with the same keys, see `Zone.from_dict`).

Two separate axes per zone, never merged into one number:
- `confidence`: how sure the interpretation is (high / medium / low; "confirmed" only after a
  dig or drill result is stored in `ground_truth`).
- `action`: what to do about it (E emergency / 1 urgent / 2 investigate / 3 monitor / none).
No percentages: confidence stays a rule-based grade with reasons until calibrated on ground truth.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from gpr_analyzer.core import depth_from_time

SCHEMA_VERSION = "0.1"

CONFIDENCE_LEVELS = ("confirmed", "high", "medium", "low")
ACTION_LEVELS = ("E", "1", "2", "3", "none")
ZONE_TYPES = ("void_like", "pipe_point", "layer", "scattered", "artifact", "unknown")
EVIDENCE_KINDS = ("present", "absent", "warning", "not_assessed")  # ✓ / ✗ / ⚠ / –
REVIEW_STATES = ("auto", "accepted", "edited", "rejected")
SURFACE_USES = ("road", "building", "open", "unknown")


def _check(value: str, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")
    return value


@dataclass
class Evidence:
    """One reason behind a zone grade.

    kind: present (✓ feature found), absent (✗ expected feature not found), warning (⚠ nearby artifact),
          not_assessed (– could not be checked, e.g. roof polarity after background removal).
    code: stable identifier, translated by report.i18n (e.g. "strong_reflection").
    text: Thai sentence from the producer; params["text_en"] holds the English one. When given,
          these win over the i18n translation of the code, since they carry the measured values.
    params: values substituted into the translated sentence, e.g. {"traces": "365–385"}.
    """

    kind: str
    code: str
    text: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # older detect output sends not-assessed signs as warning + params.not_assessed
        if self.kind == "warning" and self.params.get("not_assessed"):
            self.kind = "not_assessed"
        _check(self.kind, EVIDENCE_KINDS, "Evidence.kind")


@dataclass
class Zone:
    """One anomaly zone on one line. Depth is always a range, with the velocity it came from."""

    zone_id: str
    line_id: str
    trace_start: int
    trace_end: int
    time_top_ns: float
    time_bottom_ns: float
    velocity_m_per_ns: float
    velocity_source: str  # "hyperbola_fit" | "default" | "user" | "cmp" ...
    suspected_type: str
    confidence: str
    action: str | None = None  # None = let report.rules.suggest_action decide
    distance_start_m: float | None = None
    distance_end_m: float | None = None
    coord_start: tuple[float, float] | None = None  # (x/lon, y/lat)
    coord_end: tuple[float, float] | None = None
    datum: str | None = None  # e.g. "WGS84", "UTM 47N"
    velocity_uncertainty_m_per_ns: float | None = None
    evidence: list[Evidence] = field(default_factory=list)
    artifact_flags: list[str] = field(default_factory=list)
    review: str = "auto"
    note: str = ""
    ground_truth: dict[str, Any] | None = None  # {"method": "drill", "date": ..., "result": ...}
    antenna_separation_m: float = 0.0
    surface_elevation_start_m: float | None = None  # recorded surface altitude, not target depth
    surface_elevation_end_m: float | None = None

    def __post_init__(self) -> None:
        _check(self.suspected_type, ZONE_TYPES, "Zone.suspected_type")
        _check(self.confidence, CONFIDENCE_LEVELS, "Zone.confidence")
        if self.action is not None:
            self.action = str(self.action)
            _check(self.action, ACTION_LEVELS, "Zone.action")
        _check(self.review, REVIEW_STATES, "Zone.review")
        if self.confidence == "confirmed" and not self.ground_truth:
            raise ValueError("confidence 'confirmed' requires a ground_truth record (dig/drill result)")
        if self.trace_end < self.trace_start:
            raise ValueError("trace_end must be >= trace_start")
        if self.time_bottom_ns < self.time_top_ns:
            raise ValueError("time_bottom_ns must be >= time_top_ns")
        self.evidence = [e if isinstance(e, Evidence) else Evidence(**e) for e in self.evidence]
        if self.coord_start is not None:
            self.coord_start = tuple(self.coord_start)
        if self.coord_end is not None:
            self.coord_end = tuple(self.coord_end)

    @property
    def depth_top_m(self) -> float:
        return float(depth_from_time(self.time_top_ns, self.velocity_m_per_ns, self.antenna_separation_m))

    @property
    def depth_bottom_m(self) -> float:
        return float(depth_from_time(self.time_bottom_ns, self.velocity_m_per_ns, self.antenna_separation_m))

    def depth_range_with_uncertainty(self) -> tuple[float, float]:
        """Shallowest top and deepest bottom once the velocity uncertainty is applied."""
        dv = self.velocity_uncertainty_m_per_ns or 0.0
        return (float(depth_from_time(self.time_top_ns, self.velocity_m_per_ns - dv, self.antenna_separation_m)),
                float(depth_from_time(self.time_bottom_ns, self.velocity_m_per_ns + dv, self.antenna_separation_m)))

    @property
    def length_m(self) -> float | None:
        if self.distance_start_m is None or self.distance_end_m is None:
            return None
        return abs(self.distance_end_m - self.distance_start_m)

    @property
    def is_artifact(self) -> bool:
        return self.suspected_type == "artifact"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["depth_top_m"] = round(self.depth_top_m, 3)
        d["depth_bottom_m"] = round(self.depth_bottom_m, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Zone":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ProcessingStep:
    """One processing step as it was run, so the report can list every filter and value used."""

    name: str  # code, translated by i18n (e.g. "dewow", "bandpass")
    params: dict[str, Any] = field(default_factory=dict)
    auto: bool = True  # chosen by the program (True) or set by the user (False)


@dataclass
class ArtifactRegion:
    """Trace range flagged as artifact (hatched on the figures)."""

    trace_start: int
    trace_end: int
    reason: str  # code, e.g. "vertical_stripe", "gain_noise_floor"
    time_top_ns: float | None = None  # None = full time window
    time_bottom_ns: float | None = None


@dataclass
class LineResult:
    """Everything the report needs about one survey line."""

    line_id: str
    source_file: str
    n_traces: int
    n_samples: int
    dt_ns: float
    trace_spacing_m: float | None
    velocity_m_per_ns: float
    velocity_source: str
    processing: list[ProcessingStep] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    artifact_regions: list[ArtifactRegion] = field(default_factory=list)
    # Not serialised: processed data for figures. Any object with .data (samples, traces) and
    # .dt_ns works, normally gpr_analyzer.Radargram.
    radargram: Any = field(default=None, repr=False, compare=False)
    antenna_separation_m: float = 0.0
    figure_cache: dict | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.processing = [p if isinstance(p, ProcessingStep) else ProcessingStep(**p) for p in self.processing]
        self.zones = [z if isinstance(z, Zone) else Zone.from_dict(z) for z in self.zones]
        if self.radargram is not None:
            self.antenna_separation_m = getattr(self.radargram, "metadata", {}).get("antenna_separation_m") or 0.0
        for z in self.zones:
            z.antenna_separation_m = self.antenna_separation_m
        self.artifact_regions = [a if isinstance(a, ArtifactRegion) else ArtifactRegion(**a)
                                 for a in self.artifact_regions]

    @property
    def time_window_ns(self) -> float:
        return self.n_samples * self.dt_ns

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_id": self.line_id, "source_file": self.source_file,
            "n_traces": self.n_traces, "n_samples": self.n_samples, "dt_ns": self.dt_ns,
            "trace_spacing_m": self.trace_spacing_m,
            "velocity_m_per_ns": self.velocity_m_per_ns, "velocity_source": self.velocity_source,
            "antenna_separation_m": self.antenna_separation_m,
            "processing": [asdict(p) for p in self.processing],
            "zones": [z.to_dict() for z in self.zones],
            "artifact_regions": [asdict(a) for a in self.artifact_regions],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LineResult":
        known = {f.name for f in fields(cls)} - {"radargram", "figure_cache"}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SurveyInfo:
    """Job details for the report header. Empty strings are printed as "not recorded"."""

    project: str = ""
    site: str = ""
    client: str = ""
    survey_date: str = ""
    operator: str = ""
    instrument: str = ""
    antenna: str = ""
    frequency_mhz: float | None = None
    surface_use: str = "unknown"  # road / building / open: drives the suggested action level
    datum: str = ""
    report_date: str = ""
    report_id: str = ""

    def __post_init__(self) -> None:
        _check(self.surface_use, SURFACE_USES, "SurveyInfo.surface_use")


@dataclass
class SurveyResult:
    """One report = one survey (many lines)."""

    info: SurveyInfo
    lines: list[LineResult] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)  # extra, each cites zone_ids

    def __post_init__(self) -> None:
        if isinstance(self.info, dict):
            self.info = SurveyInfo(**self.info)
        self.lines = [ln if isinstance(ln, LineResult) else LineResult.from_dict(ln) for ln in self.lines]

    @property
    def zones(self) -> list[Zone]:
        return [z for ln in self.lines for z in ln.zones]

    @property
    def unreviewed(self) -> bool:
        """True while any zone is still only machine-proposed: the report is then a draft."""
        return any(z.review == "auto" for z in self.zones)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "info": asdict(self.info),
                "lines": [ln.to_dict() for ln in self.lines],
                "recommendations": list(self.recommendations)}

    def to_json(self, **kw: Any) -> str:
        kw.setdefault("ensure_ascii", False)
        kw.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SurveyResult":
        return cls(info=d.get("info", {}), lines=d.get("lines", []),
                   recommendations=d.get("recommendations", []))

    @classmethod
    def from_json(cls, s: str) -> "SurveyResult":
        return cls.from_dict(json.loads(s))
