"""Command line: gpr-analyzer analyze <files> --report out.pdf

Wires readers → processing → detect → report. Stages that are not merged yet are skipped with a
note on stderr, so the command already produces a report (with figures, no zones) end to end.

Expected hooks (first name found is used):
    gpr_analyzer.io:          read_file(path) | read(path)        -> Radargram | list[Radargram]
    gpr_analyzer.processing:  auto_process(r) | run_pipeline(r)   -> Radargram | (Radargram, steps)
    gpr_analyzer.detect:      detect_zones(r, line_id=...)        -> list[Zone | dict] | (zones, artifact_regions)
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any, Callable

from gpr_analyzer.report import FORMATS, write_report
from gpr_analyzer.report.schema import ArtifactRegion, LineResult, ProcessingStep, SurveyInfo, SurveyResult

HOOKS = {
    "read": ("gpr_analyzer.io", ("read_file", "read")),
    "process": ("gpr_analyzer.processing", ("auto_process", "run_pipeline")),
    "detect": ("gpr_analyzer.detect", ("detect_zones", "detect")),
}


def find_hook(stage: str) -> Callable | None:
    module, names = HOOKS[stage]
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return None
    for n in names:
        fn = getattr(mod, n, None)
        if callable(fn):
            return fn
    return None


def _note(msg: str) -> None:
    print(f"gpr-analyzer: {msg}", file=sys.stderr)


def _as_steps(steps: Any) -> list[ProcessingStep]:
    out = []
    for s in steps or []:
        if isinstance(s, ProcessingStep):
            out.append(s)
        elif isinstance(s, dict):
            out.append(ProcessingStep(**s))
        elif isinstance(s, (tuple, list)) and s:
            out.append(ProcessingStep(str(s[0]), dict(s[1]) if len(s) > 1 else {}))
    return out


def analyze_radargram(r: Any, line_id: str, source: str, velocity: float | None = None,
                      hooks: dict[str, Callable | None] | None = None) -> LineResult:
    hooks = hooks if hooks is not None else {k: find_hook(k) for k in ("process", "detect")}
    vsrc = r.metadata.get("velocity_source", "file_header" if "velocity_m_per_ns" in r.metadata else "default")
    if velocity is not None:
        r = r.copy_with(velocity_m_per_ns=velocity)
        r.metadata["velocity_source"] = vsrc = "user"  # processing and detect keep it instead of fitting
    steps: list[ProcessingStep] = []
    if hooks.get("process"):
        res = hooks["process"](r)
        if isinstance(res, tuple):
            r, steps = res[0], _as_steps(res[1])
        else:
            r, steps = res, _as_steps(getattr(res, "metadata", {}).get("processing"))
        vsrc = r.metadata.get("velocity_source", vsrc) if velocity is None else "user"
    zones, regions = [], []
    if hooks.get("detect"):
        res = hooks["detect"](r, line_id=line_id)
        zones, regions = (res if isinstance(res, tuple) else (res, []))
        regions = [a if isinstance(a, ArtifactRegion) else ArtifactRegion(**a) for a in regions]
    return LineResult(line_id=line_id, source_file=source, n_traces=r.n_traces, n_samples=r.n_samples,
                      dt_ns=r.dt_ns, trace_spacing_m=r.dx_m, velocity_m_per_ns=r.velocity_m_per_ns,
                      velocity_source=vsrc, processing=steps, zones=list(zones), artifact_regions=regions,
                      radargram=r)


def build_survey(files: list[str], info: SurveyInfo, velocity: float | None = None) -> SurveyResult:
    read = find_hook("read")
    if read is None:
        raise SystemExit("gpr-analyzer: no file reader is installed yet (gpr_analyzer.io.read_file); "
                         "use --demo to try the report with mock data")
    hooks = {k: find_hook(k) for k in ("process", "detect")}
    for k, fn in hooks.items():
        if fn is None:
            _note(f"{k} stage not available yet; skipped")
    lines = []
    for f in files:
        rs = read(f)
        rs = rs if isinstance(rs, (list, tuple)) else [rs]
        for ch, r in enumerate(rs):
            n = len(lines) + 1
            line_id = f"L{n}" if len(rs) == 1 else f"L{n}-ch{ch + 1}"
            lines.append(analyze_radargram(r, line_id, Path(f).name, velocity, hooks))
    return SurveyResult(info=info, lines=lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gpr-analyzer", description="Automatic GPR analysis and reporting")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="read files, process, detect zones and write a report")
    a.add_argument("files", nargs="*", help="raw GPR files (one line each)")
    a.add_argument("--report", "-o", action="append", required=True,
                   help=f"output file, repeatable; format from extension {FORMATS}")
    a.add_argument("--lang", choices=("th", "en", "bi"), default="bi", help="report language (default bi)")
    a.add_argument("--velocity", type=float, help="wave velocity in m/ns (overrides automatic value)")
    a.add_argument("--surface", choices=("road", "building", "open", "unknown"), default="unknown",
                   help="surface use above the survey, drives the suggested action level")
    a.add_argument("--project", default="")
    a.add_argument("--site", default="")
    a.add_argument("--client", default="")
    a.add_argument("--date", default="", help="survey date")
    a.add_argument("--operator", default="")
    a.add_argument("--report-id", default="")
    a.add_argument("--demo", action="store_true", help="ignore files and use the built-in mock survey")
    args = p.parse_args(argv)

    if args.demo:
        from gpr_analyzer.report.demo import mock_survey
        survey = mock_survey()
    else:
        if not args.files:
            p.error("give at least one file, or --demo")
        info = SurveyInfo(project=args.project, site=args.site, client=args.client, survey_date=args.date,
                          operator=args.operator, surface_use=args.surface, report_id=args.report_id)
        survey = build_survey(args.files, info, args.velocity)
    for out in args.report:
        path = write_report(survey, out, lang=args.lang)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
