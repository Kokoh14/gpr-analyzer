"""Versioned, data-free processing recipes and their observed history."""
from __future__ import annotations

import json
import math
from dataclasses import asdict

from gpr_analyzer.processing.auto import ProcessingOptions, run_pipeline

SCHEMA = "gpr-analyzer.workflow"
TRACKS = {"analysis", "display", "layers", "envelope", "energy", "viewpoint", "autodisplay"}


def options_from_dict(values: dict) -> ProcessingOptions:
    if not isinstance(values, dict):
        raise ValueError("options must be an object")
    defaults = asdict(ProcessingOptions())
    unknown = values.keys() - defaults.keys()
    if unknown:
        raise ValueError(f"unknown processing options: {', '.join(sorted(unknown))}")
    clean = {**defaults, **values}
    enums = {"background": {"sliding", "global", "none"}, "background_statistic": {"median", "mean"},
             "time_zero_source": {"auto", "header", "pick"}, "migration": {"none", "stolt", "kirchhoff"}}
    for key, value in clean.items():
        if key in enums:
            if not isinstance(value, str) or value not in enums[key]:
                raise ValueError(f"invalid {key}")
        elif key in {"time_zero", "estimate_velocity"}:
            if type(value) is not bool:
                raise ValueError(f"{key} must be boolean")
        elif key == "band_mhz":
            if value is not None:
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ValueError("band_mhz needs [low, high]")
                if not all(type(x) in (int, float) and math.isfinite(x) for x in value) or not 0 < value[0] < value[1]:
                    raise ValueError("band_mhz needs finite 0 < low < high")
                clean[key] = tuple(value)
        elif value is None and defaults[key] is None:
            continue
        elif type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (value == 0 and key != "gain_max_db"):
            raise ValueError(f"{key} needs a finite positive number")
    if clean["time_zero_threshold"] > 1:
        raise ValueError("time_zero_threshold must be <= 1")
    if clean["velocity_m_per_ns"] is not None and clean["velocity_m_per_ns"] > .3:
        raise ValueError("velocity_m_per_ns must be <= 0.3")
    return ProcessingOptions(**clean)


def export_workflow(result, options: ProcessingOptions | None = None) -> dict:
    """Keep original overrides, including None/auto; rounded log values are not execution settings."""
    opt = options if options is not None else getattr(result, "options", None)
    if opt is None:
        raise ValueError("this old result has no option snapshot; provide its saved ProcessingOptions")
    tracks = getattr(result, "requested_tracks", None)
    if tracks is None:
        tracks = [name for name in TRACKS if getattr(result, name, None) is not None]
    return {"schema": SCHEMA, "version": 1, "options": asdict(opt), "tracks": sorted(tracks),
            "history": result.parameter_table()}


def load_workflow(document: str | dict) -> tuple[ProcessingOptions, set[str]]:
    try:
        data = json.loads(document) if isinstance(document, str) else document
        if len(json.dumps(data, allow_nan=False)) > 1_000_000:
            raise ValueError("workflow exceeds 1 MB")
    except (TypeError, json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"invalid workflow JSON: {e}") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("unsupported workflow schema/version")
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or not all(isinstance(t, str) and t in TRACKS for t in tracks) or "analysis" not in tracks:
        raise ValueError("workflow tracks must include analysis and contain only supported tracks")
    if not isinstance(data.get("history"), list):
        raise ValueError("workflow history must be a list")
    return options_from_dict(data.get("options")), set(tracks)


def run_workflow(radargram, document):
    options, tracks = load_workflow(document)
    return run_pipeline(radargram, options, tracks=tracks)
