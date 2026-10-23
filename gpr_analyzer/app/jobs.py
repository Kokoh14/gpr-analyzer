"""Jobs held by the local app: uploaded files → processed lines → zones under review.

One job = one survey = one report. Each line keeps its ProcessingResult (all tracks) and its
zones as report.schema.Zone records, so the review screen, the report and the CSV all read the
same objects. Every review decision is appended to labels.jsonl in the job folder: those
records are the training/calibration data the project plans to collect.
"""

from __future__ import annotations

import io
import json
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from gpr_analyzer.app import compare as cmp
from gpr_analyzer.app.files import InputGroup, preview_text, safe_name, sort_files
from gpr_analyzer.core import Radargram
from gpr_analyzer.detect import analyze_line, to_report_records
from gpr_analyzer.io import read as read_file
from gpr_analyzer.processing import ProcessingOptions, ProcessingResult, run_pipeline
from gpr_analyzer.report import write_report
from gpr_analyzer.report.rules import suggest_action
from gpr_analyzer.report.schema import (ACTION_LEVELS, CONFIDENCE_LEVELS, SURFACE_USES, ZONE_TYPES, ArtifactRegion,
                                        Evidence, LineResult, ProcessingStep, SurveyInfo, SurveyResult, Zone)

APP_VERSION = "0.1"
VIEWS = ("grey", "high_gain", "envelope")
EDITABLE = ("trace_start", "trace_end", "time_top_ns", "time_bottom_ns", "suspected_type", "confidence", "action",
            "note")
# Processing options the screen exposes; everything else stays automatic.
OPTION_FIELDS = ("velocity_m_per_ns", "center_freq_mhz", "band_mhz", "background", "background_window_m",
                 "gain_max_db", "agc_window_ns", "time_zero", "migration")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_options(d: dict[str, Any] | None) -> ProcessingOptions:
    """ProcessingOptions from the form; blank values mean 'automatic'."""
    opt = ProcessingOptions()
    for k, v in (d or {}).items():
        if k not in OPTION_FIELDS or v in (None, "", []):
            continue
        if k == "band_mhz":
            lo, hi = (float(x) for x in v)
            if not 0 < lo < hi:
                raise ValueError("band_mhz needs 0 < low < high")
            v = (lo, hi)
        elif k == "time_zero":
            v = bool(v)
        elif k in ("background", "migration"):
            v = str(v)
        else:
            v = float(v)
            if v <= 0:
                raise ValueError(f"{k} must be positive")
        setattr(opt, k, v)
    if opt.background not in ("sliding", "global", "none"):
        raise ValueError("background must be sliding, global or none")
    if opt.migration not in ("none", "stolt", "kirchhoff"):
        raise ValueError("migration must be none, stolt or kirchhoff")
    return opt


def options_dict(opt: ProcessingOptions) -> dict[str, Any]:
    """Only the fields the user changed from the automatic defaults."""
    base = ProcessingOptions()
    return {f.name: getattr(opt, f.name) for f in fields(opt)
            if f.name in OPTION_FIELDS and getattr(opt, f.name) != getattr(base, f.name)}


@dataclass
class LineState:
    line_id: str
    source_file: str
    channel: int | None
    raw: Radargram
    companions: list[dict[str, Any]] = field(default_factory=list)
    options: ProcessingOptions = field(default_factory=ProcessingOptions)
    result: ProcessingResult | None = None
    zones: list[Zone] = field(default_factory=list)
    artifact_regions: list[ArtifactRegion] = field(default_factory=list)
    auto_zones: dict[str, dict[str, Any]] = field(default_factory=dict)  # as proposed, for the labels
    _png: dict[str, bytes] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ processing
    def process(self) -> None:
        res = run_pipeline(self.raw, self.options)
        det = analyze_line(res.analysis, velocity_m_per_ns=res.velocity_m_per_ns,
                           velocity_source=res.velocity_source_code)
        zones, regions = to_report_records(det, line_id=self.line_id)
        self.result = res
        self.zones = [Zone.from_dict(z) for z in zones]
        self.artifact_regions = [ArtifactRegion(**a) for a in regions]
        self.auto_zones = {z.zone_id: z.to_dict() for z in self.zones}
        self._png.clear()

    @property
    def analysis(self) -> Radargram:
        assert self.result is not None
        return self.result.analysis

    def zone(self, zone_id: str) -> Zone:
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        raise KeyError(zone_id)

    # ------------------------------------------------------------------ images
    def png(self, view: str) -> bytes:
        if view not in VIEWS:
            raise KeyError(view)
        if view not in self._png:
            self._png[view] = render_png(self.result, view)
        return self._png[view]

    def line_result(self) -> LineResult:
        res = self.result
        return LineResult(
            line_id=self.line_id, source_file=self.source_file, n_traces=res.analysis.n_traces,
            n_samples=res.analysis.n_samples, dt_ns=res.analysis.dt_ns, trace_spacing_m=res.analysis.dx_m,
            velocity_m_per_ns=res.velocity_m_per_ns, velocity_source=res.velocity_source_code,
            processing=[ProcessingStep(**s) for s in res.report_steps()], zones=list(self.zones),
            artifact_regions=list(self.artifact_regions), radargram=res.analysis)

    def to_json(self, surface_use: str) -> dict[str, Any]:
        res = self.result
        a = res.analysis
        zones = []
        for z in self.zones:
            d = z.to_dict()
            d["suggested_action"] = suggest_action(z, surface_use)
            d["manual"] = z.zone_id not in self.auto_zones
            zones.append(d)
        return {
            "line_id": self.line_id, "source_file": self.source_file, "channel": self.channel,
            "n_traces": a.n_traces, "n_samples": a.n_samples, "dt_ns": a.dt_ns, "dx_m": a.dx_m,
            "time_window_ns": a.n_samples * a.dt_ns,
            "velocity_m_per_ns": res.velocity_m_per_ns, "velocity_source": res.velocity_source,
            "velocity_source_code": res.velocity_source_code,
            "hyperbola_fits": len(res.hyperbola_fits),
            "metadata": {k: v for k, v in self.raw.metadata.items() if isinstance(v, (str, int, float, bool))},
            "companions": self.companions,
            "steps": [s.to_dict() for s in res.log],
            "options": options_dict(self.options),
            "zones": zones,
            "artifact_regions": [asdict(r) for r in self.artifact_regions],
            "review": review_counts(self.zones),
        }


def review_counts(zones: list[Zone]) -> dict[str, int]:
    out = {"auto": 0, "accepted": 0, "edited": 0, "rejected": 0}
    for z in zones:
        out[z.review] += 1
    return out


def render_png(res: ProcessingResult, view: str) -> bytes:
    """Bare image, one pixel per sample and trace. Axes and zone boxes are drawn by the page."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import colormaps
    from matplotlib.image import imsave

    if view == "grey":
        d = res.analysis.data
        c = float(np.percentile(np.abs(d), 99.0)) or 1.0
        img, cmap, lo, hi = d, "gray", -c, c
    elif view == "high_gain":
        d = res.display.data
        c = float(np.percentile(np.abs(d), 95.0)) or 1.0
        img, cmap, lo, hi = d, "seismic", -c, c
    else:
        env = np.abs(res.envelope.data).astype(np.float64)
        ref = env.max() if env.size and env.max() > 0 else 1.0
        img = 20.0 * np.log10(np.maximum(env / ref, 10 ** (-30.0 / 20.0)))
        cmap, lo, hi = "turbo", -30.0, 0.0
    buf = io.BytesIO()
    imsave(buf, np.asarray(img, dtype=np.float64), cmap=colormaps[cmap], vmin=lo, vmax=hi, format="png")
    return buf.getvalue()


@dataclass
class Job:
    job_id: str
    folder: Path
    info: SurveyInfo = field(default_factory=SurveyInfo)
    lang: str = "bi"
    reviewer: str = ""
    lines: list[LineState] = field(default_factory=list)
    problems: list[dict[str, str]] = field(default_factory=list)  # files that could not be read
    unassigned: list[dict[str, Any]] = field(default_factory=list)
    created: str = field(default_factory=_now)
    comparison: dict[str, Any] = field(default_factory=cmp.new_state)  # see compare.py
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def upload_dir(self) -> Path:
        return self.folder / "uploads"

    @property
    def labels_path(self) -> Path:
        return self.folder / "labels.jsonl"

    @property
    def ref_dir(self) -> Path:
        return self.folder / "reference"

    def line(self, line_id: str) -> LineState:
        for ln in self.lines:
            if ln.line_id == line_id:
                return ln
        raise KeyError(line_id)

    # ------------------------------------------------------------------ loading
    def load(self, paths: list[Path]) -> None:
        s = sort_files(paths)
        for g in s.groups:
            self._load_group(g)
        self.unassigned = [_companion(p) for p in s.unassigned]
        for p in s.unassigned:
            if p.suffix.lower() not in {".txt", ".csv", ".cor", ".gps", ".xml", ".ini", ".json", ".log"}:
                self.problems.append({"file": p.name, "error": "unknown_format"})
        if not self.lines and not self.problems:
            self.problems.append({"file": "", "error": "no_files"})

    def _load_group(self, g: InputGroup) -> None:
        try:
            rgs = read_file(g.primary)
        except Exception as e:  # noqa: BLE001 - reported to the user per file
            self.problems.append({"file": g.primary.name, "error": f"{type(e).__name__}: {e}"})
            return
        comps = [_companion(p) for p in g.companions]
        for ch, rg in enumerate(rgs):
            n = len(self.lines) + 1
            line_id = f"L{n}" if len(rgs) == 1 else f"L{n}-ch{ch + 1}"
            ln = LineState(line_id=line_id, source_file=g.primary.name, channel=ch + 1 if len(rgs) > 1 else None,
                           raw=rg, companions=comps)
            try:
                ln.process()
            except Exception as e:  # noqa: BLE001
                self.problems.append({"file": g.primary.name, "error": f"processing failed: {type(e).__name__}: {e}"})
                continue
            self.lines.append(ln)

    # ------------------------------------------------------------------ review
    def review(self, line_id: str, zone_id: str, action: str, changes: dict[str, Any] | None = None,
               note: str | None = None) -> Zone:
        """accept | reject | edit | reset. Returns the zone after the change and logs a label."""
        ln = self.line(line_id)
        z = ln.zone(zone_id)
        before = z.to_dict()
        if action == "accept":
            new = _replace(z, review="accepted")
        elif action == "reject":
            new = _replace(z, review="rejected")
        elif action == "reset":
            if zone_id not in ln.auto_zones:
                raise ValueError("a zone drawn by the reviewer has no automatic version; delete it instead")
            new = Zone.from_dict(ln.auto_zones[zone_id])
        elif action == "edit":
            new = _replace(z, review="edited", **_clean_changes(changes or {}, ln))
        else:
            raise ValueError(f"unknown review action {action!r}")
        if note is not None:
            new = _replace(new, note=str(note))
        ln.zones[ln.zones.index(z)] = new
        self._label(action, ln, zone_id, before, new.to_dict())
        return new

    def add_zone(self, line_id: str, values: dict[str, Any]) -> Zone:
        """Zone drawn by the reviewer (something the detector missed)."""
        ln = self.line(line_id)
        used = {z.zone_id for z in ln.zones}
        k = 1
        while f"M{k}" in used:
            k += 1
        res = ln.result
        base = {"zone_id": f"M{k}", "line_id": line_id, "velocity_m_per_ns": res.velocity_m_per_ns,
                "velocity_source": res.velocity_source_code, "suspected_type": "unknown", "confidence": "low",
                "review": "edited", "evidence": [{"kind": "present", "code": "manual",
                                                  "text": "วาดเพิ่มโดยผู้ตรวจทาน",
                                                  "params": {"text_en": "drawn by the reviewer"}}]}
        base.update(_clean_changes(values, ln))
        for key in ("trace_start", "trace_end", "time_top_ns", "time_bottom_ns"):
            if key not in base:
                raise ValueError(f"{key} is required")
        if ln.analysis.dx_m is not None:
            base["distance_start_m"] = base["trace_start"] * ln.analysis.dx_m
            base["distance_end_m"] = base["trace_end"] * ln.analysis.dx_m
        z = Zone.from_dict(base)
        ln.zones.append(z)
        self._label("add", ln, z.zone_id, None, z.to_dict())
        return z

    def delete_zone(self, line_id: str, zone_id: str) -> None:
        ln = self.line(line_id)
        if zone_id in ln.auto_zones:
            raise ValueError("automatic zones are rejected, not deleted, so the decision is kept")
        z = ln.zone(zone_id)
        ln.zones.remove(z)
        self._label("delete", ln, zone_id, z.to_dict(), None)

    def reprocess(self, line_id: str, options: dict[str, Any]) -> LineState:
        ln = self.line(line_id)
        opt = parse_options(options)
        dropped = [z.to_dict() for z in ln.zones if z.review != "auto"]
        ln.options = opt
        ln.process()
        self._label("reprocess", ln, None, {"reviewed_zones_discarded": dropped}, {"options": options_dict(opt)})
        return ln

    def _label(self, action: str, ln: LineState, zone_id: str | None, before: Any, after: Any) -> None:
        res = ln.result
        rec = {
            "time": _now(), "app_version": APP_VERSION, "job_id": self.job_id, "reviewer": self.reviewer,
            "action": action, "line_id": ln.line_id, "source_file": ln.source_file, "zone_id": zone_id,
            "auto": ln.auto_zones.get(zone_id) if zone_id else None, "before": before, "after": after,
            "processing": {"options": options_dict(ln.options), "velocity_m_per_ns": res.velocity_m_per_ns,
                           "velocity_source": res.velocity_source_code},
        }
        with self.labels_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.save()

    # ------------------------------------------------------------------ output
    def survey(self) -> SurveyResult:
        return SurveyResult(info=self.info, lines=[ln.line_result() for ln in self.lines])

    def save(self) -> None:
        (self.folder / "survey.json").write_text(self.survey().to_json(), encoding="utf-8")
        comp = self.comparison_json()
        zones = [z for ln in self.lines for z in ln.zones]
        state = {
            "version": 1, "job_id": self.job_id, "created": self.created, "saved": _now(), "info": asdict(self.info),
            "lang": self.lang, "reviewer": self.reviewer,
            "lines": [{"line_id": ln.line_id, "source_file": ln.source_file, "options": options_dict(ln.options),
                       "zones": [z.to_dict() for z in ln.zones], "auto_zones": ln.auto_zones} for ln in self.lines],
            "comparison": self.comparison,
            # read by the job list and the all-jobs comparison export without reprocessing
            "summary": {"n_lines": len(self.lines), "files": sorted({ln.source_file for ln in self.lines}),
                        "review": review_counts(zones), "compare": comp["total"]},
            "compare_lines": [{"line_id": k, **{x: v[x] for x in ("source_file", "summary", "meta_check")}}
                              for k, v in comp["lines"].items()],
            "compare_rows": self.comparison_rows(comp),
        }
        tmp = self.folder / "state.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.folder / "state.json")

    @classmethod
    def restore(cls, folder: Path) -> "Job":
        """Reopen a job saved by an earlier run: re-read the uploads, then put back options, zones and reviews."""
        st = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        job = cls(job_id=st["job_id"], folder=folder, created=st.get("created", _now()))
        job.info = SurveyInfo(**{k: v for k, v in st.get("info", {}).items() if k in {f.name for f in fields(SurveyInfo)}})
        job.lang, job.reviewer = st.get("lang", "bi"), st.get("reviewer", "")
        job.comparison = st.get("comparison") or cmp.new_state()
        job.load(sorted(p for p in job.upload_dir.iterdir() if p.is_file()))
        saved = {d["line_id"]: d for d in st.get("lines", [])}
        for ln in job.lines:
            d = saved.get(ln.line_id)
            if not d or d.get("source_file") != ln.source_file:
                continue
            if d.get("options"):
                ln.options = parse_options(d["options"])
                ln.process()
            ln.zones = [Zone.from_dict(z) for z in d.get("zones", [])]
            ln.auto_zones = d.get("auto_zones", ln.auto_zones)
        return job

    def export(self, name: str) -> Path:
        """Write report.pdf/.docx/.html, zones.csv or survey.json into the job folder."""
        out = self.folder / "exports"
        out.mkdir(exist_ok=True)
        allowed = {"report.pdf", "report.docx", "report.html", "zones.csv", "survey.json"}
        if name not in allowed:
            raise KeyError(name)
        return write_report(self.survey(), out / name, lang=self.lang)

    def export_line(self, line_id: str, fmt: str) -> Path:
        """Processed (analysis-track) data of one line as SEG-Y or CSV."""
        ln = self.line(line_id)
        out = self.folder / "exports"
        out.mkdir(exist_ok=True)
        a = ln.analysis.copy_with(velocity_m_per_ns=ln.result.velocity_m_per_ns)
        if fmt == "sgy":
            from gpr_analyzer.io import write_segy
            path = out / f"{line_id}_processed.sgy"
            write_segy(a, path)
        elif fmt == "csv":
            path = out / f"{line_id}_processed.csv"
            x = a.distance_axis()
            head = "time_ns\\" + ("distance_m" if a.dx_m is not None else "trace")
            with path.open("w", encoding="utf-8") as f:
                f.write(head + "," + ",".join(f"{v:g}" for v in x) + "\n")
                for t, row in zip(a.time_axis(), a.data):
                    f.write(f"{t:.4f}," + ",".join(f"{v:.6g}" for v in row) + "\n")
        else:
            raise KeyError(fmt)
        return path

    def to_json(self) -> dict[str, Any]:
        zones = [z for ln in self.lines for z in ln.zones]
        return {
            "job_id": self.job_id, "created": self.created, "info": asdict(self.info), "lang": self.lang,
            "reviewer": self.reviewer, "problems": self.problems, "unassigned": self.unassigned,
            "review": review_counts(zones), "draft": any(z.review == "auto" for z in zones),
            "lines": [ln.to_json(self.info.surface_use) for ln in self.lines],
            "comparison": {**self.comparison_json(), "tolerance": self.comparison["tolerance"],
                           "features": self.comparison["features"]},
            "choices": {"suspected_type": ZONE_TYPES, "confidence": [c for c in CONFIDENCE_LEVELS if c != "confirmed"],
                        "action": ACTION_LEVELS, "surface_use": SURFACE_USES, "ref_source": cmp.SOURCES,
                        "ref_verdict": cmp.REF_VERDICTS, "zone_verdict": cmp.ZONE_VERDICTS,
                        "meta_fields": cmp.META_FIELDS, "feature_rating": cmp.FEATURE_RATINGS},
        }

    # ------------------------------------------------------------------ comparison with reference software
    def geom(self, ln: LineState) -> cmp.LineGeom:
        a = ln.analysis
        return cmp.LineGeom(n_traces=a.n_traces, dx_m=a.dx_m, time_window_ns=a.n_samples * a.dt_ns,
                            velocity_m_per_ns=ln.result.velocity_m_per_ns)

    def comparison_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for ln in self.lines:
            ls = cmp.line_state(self.comparison, ln.line_id)
            res = cmp.compare_line(ls, [z.to_dict() for z in ln.zones], self.geom(ln), self.comparison["tolerance"])
            out[ln.line_id] = {"source_file": ln.source_file, "picks": ls["picks"], "images": ls["images"],
                               "meta": ls["meta"], **res}
        total = cmp.add_summaries([v["summary"] for v in out.values() if v["picks"]]) if out else None
        return {"lines": out, "total": total}

    def comparison_rows(self, comp: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        comp = comp or self.comparison_json()
        return [{"job_id": self.job_id, "project": self.info.project, "line_id": k, "source_file": v["source_file"], **r}
                for k, v in comp["lines"].items() for r in v["rows"] if v["picks"] or r["verdict_by_user"]]

    def _ref_line(self, line_id: str) -> tuple[LineState, dict[str, Any]]:
        ln = self.line(line_id)
        return ln, cmp.line_state(self.comparison, line_id)

    def add_ref_pick(self, line_id: str, d: dict[str, Any]) -> dict[str, Any]:
        ln, ls = self._ref_line(line_id)
        p = cmp.clean_pick(d, cmp.next_ref_id(ls["picks"]))
        ls["picks"].append(p)
        self._label("reference_pick", ln, None, None, p)
        return p

    def update_ref_pick(self, line_id: str, ref_id: str, d: dict[str, Any]) -> dict[str, Any]:
        ln, ls = self._ref_line(line_id)
        i = next((i for i, p in enumerate(ls["picks"]) if p["ref_id"] == ref_id), None)
        if i is None:
            raise KeyError(ref_id)
        new = cmp.clean_pick({**ls["picks"][i], **d}, ref_id)
        before, ls["picks"][i] = ls["picks"][i], new
        self._label("reference_pick_edit", ln, None, before, new)
        return new

    def delete_ref_pick(self, line_id: str, ref_id: str) -> None:
        ln, ls = self._ref_line(line_id)
        p = next((p for p in ls["picks"] if p["ref_id"] == ref_id), None)
        if p is None:
            raise KeyError(ref_id)
        ls["picks"].remove(p)
        ls["verdicts"].pop(f"ref:{ref_id}", None)
        self._label("reference_pick_delete", ln, None, p, None)

    def import_ref_csv(self, line_id: str, data: bytes, source: str, replace: bool = False) -> dict[str, Any]:
        ln, ls = self._ref_line(line_id)
        raw, warnings = cmp.parse_picks_csv(data, source)
        if replace:
            ls["picks"] = [p for p in ls["picks"] if p["source"] != source]
        added = []
        for d in raw:
            p = cmp.clean_pick(d, cmp.next_ref_id(ls["picks"]))
            ls["picks"].append(p)
            added.append(p)
        self._label("reference_import", ln, None, None, {"source": source, "picks": added})
        return {"added": len(added), "warnings": warnings}

    def set_ref_meta(self, line_id: str, source: str, d: dict[str, Any]) -> None:
        if source not in cmp.SOURCES:
            raise ValueError("unknown reference source")
        _, ls = self._ref_line(line_id)
        ls["meta"][source] = cmp.clean_meta(d)
        self.save()

    def set_verdict(self, line_id: str, key: str, verdict: str | None, note: str | None = None) -> None:
        ln, ls = self._ref_line(line_id)
        kind, _, ident = key.partition(":")
        allowed = cmp.REF_VERDICTS if kind == "ref" else cmp.ZONE_VERDICTS if kind == "zone" else None
        if allowed is None or not ident:
            raise ValueError("verdict key must be ref:<id> or zone:<id>")
        if verdict not in (None, "", *allowed):
            raise ValueError(f"verdict must be one of {', '.join(allowed)}")
        cur = ls["verdicts"].get(key, {})
        new = {"verdict": verdict or None, "note": cur.get("note", "") if note is None else str(note)[:500]}
        if new["verdict"] is None and not new["note"]:
            ls["verdicts"].pop(key, None)
        else:
            ls["verdicts"][key] = new
        self._label("compare_verdict", ln, ident if kind == "zone" else None, {"key": key, **cur}, {"key": key, **new})

    def add_ref_image(self, line_id: str, source: str, name: str, data: bytes) -> dict[str, Any]:
        if source not in cmp.SOURCES:
            raise ValueError("unknown reference source")
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_TYPES:
            raise ValueError("reference images must be PNG, JPG, GIF, BMP or WEBP")
        _, ls = self._ref_line(line_id)
        d = self.ref_dir / line_id
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex[:8]}_{safe_name(name)}"
        (d / fname).write_bytes(data)
        img = {"file": fname, "name": safe_name(name), "source": source}
        ls["images"].append(img)
        self.save()
        return img

    def ref_image_path(self, line_id: str, fname: str) -> Path:
        _, ls = self._ref_line(line_id)
        if not any(i["file"] == fname for i in ls["images"]):
            raise KeyError(fname)
        return self.ref_dir / line_id / fname

    def delete_ref_image(self, line_id: str, fname: str) -> None:
        p = self.ref_image_path(line_id, fname)
        _, ls = self._ref_line(line_id)
        ls["images"] = [i for i in ls["images"] if i["file"] != fname]
        p.unlink(missing_ok=True)
        self.save()

    def set_compare_settings(self, d: dict[str, Any]) -> None:
        if "tolerance" in d:
            tol = dict(self.comparison["tolerance"])
            for k in cmp.DEFAULT_TOLERANCE:
                v = d["tolerance"].get(k)
                if v not in (None, ""):
                    if float(v) <= 0:
                        raise ValueError(f"tolerance {k} must be positive")
                    tol[k] = float(v)
            self.comparison["tolerance"] = tol
        if "features" in d:
            feats = []
            for f in d["features"][:100]:
                if not str(f.get("feature", "")).strip():
                    continue
                if f.get("rating") not in cmp.FEATURE_RATINGS or f.get("source") not in cmp.SOURCES:
                    raise ValueError("feature rows need a known rating and source")
                feats.append({"feature": str(f["feature"])[:120], "source": f["source"], "rating": f["rating"],
                              "note": str(f.get("note", ""))[:500]})
            self.comparison["features"] = feats
        self.save()

    def export_comparison(self, name: str) -> Path:
        out = self.folder / "exports"
        out.mkdir(exist_ok=True)
        path = out / name
        if name == "comparison.csv":
            text = cmp.rows_to_csv(self.comparison_rows(), cmp.COMPARE_COLUMNS)
        elif name == "comparison_lines.csv":
            text = cmp.rows_to_csv(line_summary_rows(self.job_id, self.info.project, self.comparison_json()),
                                   LINE_SUMMARY_COLUMNS)
        elif name == "features.csv":
            text = cmp.rows_to_csv([{"job_id": self.job_id, **f} for f in self.comparison["features"]],
                                   ("job_id", "feature", "source", "rating", "note"))
        else:
            raise KeyError(name)
        path.write_text(text, encoding="utf-8", newline="")  # text already has \r\n line ends
        return path

    def set_info(self, d: dict[str, Any]) -> None:
        known = {f.name for f in fields(SurveyInfo)}
        cur = asdict(self.info)
        cur.update({k: v for k, v in d.items() if k in known})
        if cur.get("frequency_mhz") in ("", None):
            cur["frequency_mhz"] = None
        else:
            cur["frequency_mhz"] = float(cur["frequency_mhz"])
        self.info = SurveyInfo(**cur)
        if d.get("lang") in ("th", "en", "bi"):
            self.lang = d["lang"]
        if "reviewer" in d:
            self.reviewer = str(d["reviewer"])[:80]
        self.save()


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".bmp": "image/bmp", ".webp": "image/webp"}
LINE_SUMMARY_COLUMNS = ("job_id", "project", "line_id", "source_file", "n_ref", "n_ours", "match", "partial", "missed",
                        "extra", "extra_real", "extra_false", "unsure", "recall", "precision", "mean_abs_dx_m",
                        "mean_abs_ddepth_m", "meta_checked", "meta_mismatch")


def line_summary_rows(job_id: str, project: str, comp: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per line: the counts, plus how many reference line facts disagree with ours."""
    rows = []
    for k, v in comp["lines"].items():
        if not v["picks"] and not v["meta_check"]:
            continue
        rows.append({"job_id": job_id, "project": project, "line_id": k, "source_file": v["source_file"], **v["summary"],
                     "meta_checked": len(v["meta_check"]), "meta_mismatch": sum(m["ok"] is False for m in v["meta_check"])})
    return rows


def _companion(p: Path) -> dict[str, Any]:
    return {"name": p.name, "size": p.stat().st_size, "text": preview_text(p)}


def _replace(z: Zone, **changes: Any) -> Zone:
    d = z.to_dict()
    d.update(changes)
    return Zone.from_dict(d)


def _clean_changes(changes: dict[str, Any], ln: LineState) -> dict[str, Any]:
    a = ln.analysis
    out: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in EDITABLE:
            continue
        if k in ("trace_start", "trace_end"):
            out[k] = int(min(max(int(round(float(v))), 0), a.n_traces - 1))
        elif k in ("time_top_ns", "time_bottom_ns"):
            out[k] = float(min(max(float(v), 0.0), a.n_samples * a.dt_ns))
        elif k == "action":
            out[k] = None if v in ("", None, "auto") else str(v)
        elif k == "confidence" and v == "confirmed":
            raise ValueError("'confirmed' needs a dig/drill result; keep high/medium/low until then")
        else:
            out[k] = str(v)
    for lo, hi in (("trace_start", "trace_end"), ("time_top_ns", "time_bottom_ns")):
        if lo in out and hi in out and out[hi] < out[lo]:
            out[lo], out[hi] = out[hi], out[lo]
    if a.dx_m is not None:
        if "trace_start" in out:
            out["distance_start_m"] = out["trace_start"] * a.dx_m
        if "trace_end" in out:
            out["distance_end_m"] = out["trace_end"] * a.dx_m
    return out


class JobStore:
    """Jobs in memory; files, survey.json and labels.jsonl on disk under `root`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}

    def new(self) -> Job:
        jid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job = Job(job_id=jid, folder=self.root / jid)
        job.upload_dir.mkdir(parents=True)
        self.jobs[jid] = job
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            folder = self.root / job_id
            if "/" in job_id or "\\" in job_id or job_id.startswith(".") or not (folder / "state.json").is_file():
                raise KeyError(job_id)
            self.jobs[job_id] = Job.restore(folder)
        return self.jobs[job_id]

    def states(self) -> list[dict[str, Any]]:
        """Saved state of every job in the work folder, newest first (read from disk, nothing reprocessed)."""
        out = []
        for f in sorted(self.root.glob("*/state.json"), reverse=True):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def summaries(self) -> list[dict[str, Any]]:
        return [{"job_id": s["job_id"], "created": s.get("created"), "saved": s.get("saved"),
                 "project": s.get("info", {}).get("project", ""), "site": s.get("info", {}).get("site", ""),
                 **s.get("summary", {})} for s in self.states()]

    def export_all(self, name: str) -> str:
        """Comparison of every saved job in one sheet, for many real lines tested over several sessions."""
        states = self.states()
        if name == "comparison_all.csv":
            return cmp.rows_to_csv([r for s in states for r in s.get("compare_rows", [])], cmp.COMPARE_COLUMNS)
        if name == "comparison_lines_all.csv":
            rows = [{"job_id": s["job_id"], "project": s.get("info", {}).get("project", ""), **ln, **ln["summary"],
                     "meta_checked": len(ln["meta_check"]), "meta_mismatch": sum(m["ok"] is False for m in ln["meta_check"])}
                    for s in states for ln in s.get("compare_lines", []) if ln["summary"]["n_ref"] or ln["meta_check"]]
            return cmp.rows_to_csv(rows, LINE_SUMMARY_COLUMNS)
        if name == "features_all.csv":
            rows = [{"job_id": s["job_id"], **f} for s in states for f in s.get("comparison", {}).get("features", [])
                    if f.get("rating") != "untested" or f.get("note")]
            return cmp.rows_to_csv(rows, ("job_id", "feature", "source", "rating", "note"))
        raise KeyError(name)
