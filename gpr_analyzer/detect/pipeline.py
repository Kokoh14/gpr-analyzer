"""One call per line: artifacts → hyperbolas / velocity → zones → classification → collapse risk.

Run it on the amplitude-preserving processed section (no AGC): the scores compare amplitudes,
and AGC flattens exactly the contrasts the rules look at.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gpr_analyzer.core import Radargram
from gpr_analyzer.detect._signal import contrast_section, surface_polarity
from gpr_analyzer.detect.artifacts import ArtifactParams, detect_artifacts
from gpr_analyzer.detect.classify import ClassifyParams, Context, classify_zone
from gpr_analyzer.detect.hyperbola import HyperbolaParams, estimate_velocity, find_hyperbolas
from gpr_analyzer.detect.models import DetectionResult, ZoneType
from gpr_analyzer.detect.risk import RiskContext, assess_collapse_risk, velocity_depth_uncertainty
from gpr_analyzer.detect.zones import ZoneParams, find_zones


@dataclass
class DetectParams:
    artifacts: ArtifactParams = field(default_factory=ArtifactParams)
    hyperbola: HyperbolaParams = field(default_factory=HyperbolaParams)
    zones: ZoneParams = field(default_factory=ZoneParams)
    classify: ClassifyParams = field(default_factory=ClassifyParams)
    use_hyperbola_velocity: bool = True
    min_velocity_fits: int = 3  # fewer fits than this → keep the radargram's velocity
    surface_polarity: int | None = None  # override (+1/−1) when the direct wave was removed


def analyze_line(r: Radargram, params: DetectParams | None = None, risk_context: RiskContext | None = None,
                 velocity_m_per_ns: float | None = None, velocity_source: str | None = None) -> DetectionResult:
    """Detect, classify and grade one line.

    velocity_m_per_ns / velocity_source: pass the processing stage's velocity (e.g. its
    hyperbola fit) to use it instead of this module's own estimate.
    """
    p = params or DetectParams()
    contrast = contrast_section(r.data)
    c, per, top = contrast
    artifacts = detect_artifacts(r, p.artifacts)
    hyps = find_hyperbolas(r, p.hyperbola, contrast=contrast, artifacts=artifacts)
    vel = estimate_velocity(r, p.hyperbola, hyperbolas=hyps)

    if velocity_m_per_ns is not None:
        v, v_src = velocity_m_per_ns, velocity_source or "user"
    elif p.use_hyperbola_velocity and vel and vel.n_fits >= p.min_velocity_fits:
        v, v_src = vel.velocity_m_per_ns, f"hyperbola_fit (n={vel.n_fits})"
    else:
        v = r.velocity_m_per_ns
        v_src = r.metadata.get("velocity_source", "default")
    depth_unc = velocity_depth_uncertainty(v, vel.spread_m_per_ns if vel and v_src.startswith("hyperbola") else None)

    zones = find_zones(r, artifacts, velocity_m_per_ns=v, velocity_source=v_src, params=p.zones, contrast=contrast,
                       hyperbolas=hyps)
    sign = p.surface_polarity if p.surface_polarity is not None else surface_polarity(r.data, per)
    ctx = Context(data=r.data.astype(float), contrast=c, period=per, surface_sign=sign, hyperbolas=hyps,
                  artifacts=artifacts, dt_ns=r.dt_ns)
    rc = risk_context or RiskContext()
    if rc.depth_uncertainty == RiskContext().depth_uncertainty:
        rc = RiskContext(**{**asdict(rc), "depth_uncertainty": depth_unc})
    for z in zones:
        classify_zone(z, ctx, p.classify)
        if z.suspected_type == ZoneType.VOID_LIKE:
            core = z.features.get("roof_core")
            width = z.width_m
            if core and r.dx_m and core[1] >= core[0] + 2:
                width = (core[1] - core[0] + 1) * r.dx_m  # roof extent, without the diffraction tails
            z.risk = assess_collapse_risk(z.depth_top_m, width, z.confidence, rc)

    return DetectionResult(
        n_traces=r.n_traces, n_samples=r.n_samples, dt_ns=r.dt_ns, dx_m=r.dx_m,
        parameters={"velocity_m_per_ns": v, "velocity_source": v_src, "depth_uncertainty": depth_unc,
                    "dominant_period_samples": per, "direct_wave_end_sample": top,
                    "surface_polarity": sign, **_params_dict(p)},
        artifacts=artifacts, hyperbolas=hyps, velocity=vel, zones=zones,
        source={k: v for k, v in r.metadata.items() if isinstance(v, (str, int, float, bool))})


def _params_dict(p: DetectParams) -> dict[str, Any]:
    return {"artifact_params": asdict(p.artifacts), "hyperbola_params": asdict(p.hyperbola),
            "zone_params": asdict(p.zones), "classify_params": asdict(p.classify),
            "use_hyperbola_velocity": p.use_hyperbola_velocity, "min_velocity_fits": p.min_velocity_fits}


# --------------------------------------------------------------------------- report hook

_REPORT_TYPE = {ZoneType.POINT: "pipe_point", ZoneType.UNCLASSIFIED: "unknown"}
_REPORT_KIND = {"found": "present", "absent": "absent", "warning": "warning", "not_assessed": "warning"}
_GRADE_TO_ACTION = {1: "E", 2: "1", 3: "2", 4: "3"}


def to_report_records(res: DetectionResult, line_id: str = "") -> tuple[list[dict], list[dict]]:
    """(zones, artifact_regions) as dicts in the gpr_analyzer.report.schema layout.

    Times are relative to time zero so the report's depth = v·t/2 matches ours. "not_assessed"
    evidence has no report kind yet and goes out as a warning with not_assessed=True in params.
    The action comes from the collapse-risk grade for void-like zones (I→E, II→1, III→2, IV→3);
    for other zones it is left to the report's own rules.
    """
    t0 = res.parameters.get("zone_params", {}).get("time_zero_ns", 0.0)
    unc = None
    if res.velocity and str(res.parameters.get("velocity_source", "")).startswith("hyperbola"):
        unc = res.velocity.spread_m_per_ns
    zones = []
    for z in res.zones:
        ev = [{"kind": _REPORT_KIND[e.status], "code": e.code, "text": e.text_th,
               "params": {"text_en": e.text_en, **({"not_assessed": True} if e.status == "not_assessed" else {})}}
              for e in z.evidence]
        note = ""
        action = None
        if z.risk is not None:
            rk = z.risk
            note = f"collapse risk {rk.grade_label_en}: {rk.action_en}" if rk.grade else rk.action_en
            action = _GRADE_TO_ACTION.get(rk.grade) if rk.grade else None
            ev += [{"kind": _REPORT_KIND[e.status], "code": f"risk_{e.code}", "text": e.text_th,
                    "params": {"text_en": e.text_en}} for e in rk.reasons]
        zones.append({
            "zone_id": z.id, "line_id": line_id, "trace_start": z.trace_start, "trace_end": z.trace_end,
            "time_top_ns": max(0.0, z.time_start_ns - t0), "time_bottom_ns": max(0.0, z.time_end_ns - t0),
            "velocity_m_per_ns": z.velocity_m_per_ns,
            "velocity_source": "hyperbola_fit" if z.velocity_source.startswith("hyperbola") else z.velocity_source,
            "velocity_uncertainty_m_per_ns": unc,
            "suspected_type": _REPORT_TYPE.get(z.suspected_type, z.suspected_type.value),
            "confidence": z.confidence.value, "action": action,
            "distance_start_m": z.distance_start_m, "distance_end_m": z.distance_end_m,
            "evidence": ev, "artifact_flags": list(z.nearby_artifacts),
            "review": "auto", "note": note, "ground_truth": z.ground_truth,
        })
    regions = [{"trace_start": a.trace_start, "trace_end": a.trace_end, "reason": a.kind,
                "time_top_ns": a.time_start_ns if a.kind == "bottom_noise" else None,
                "time_bottom_ns": a.time_end_ns if a.kind == "bottom_noise" else None}
               for a in res.artifacts]
    return zones, regions


def detect_zones(r: Radargram, line_id: str = "", **kw: Any) -> tuple[list[dict], list[dict]]:
    """Hook used by the CLI (gpr_analyzer.cli): analyze_line + to_report_records."""
    return to_report_records(analyze_line(r, **kw), line_id=line_id)
