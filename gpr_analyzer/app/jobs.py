"""Jobs held by the local app: uploaded files → processed lines → zones under review.

One job = one survey = one report. Each line keeps its ProcessingResult (all tracks) and its
zones as report.schema.Zone records, so the review screen, the report and the CSV all read the
same objects. Every review decision is appended to labels.jsonl in the job folder: those
records are the training/calibration data the project plans to collect.
"""

from __future__ import annotations

import io
import json
import pickle
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from weakref import WeakValueDictionary

import numpy as np

from gpr_analyzer.app import compare as cmp
from gpr_analyzer.app import geo
from gpr_analyzer.app.files import InputGroup, preview_text, safe_name, sort_files
from gpr_analyzer.core import Radargram, crop_to_range
from gpr_analyzer.detect import analyze_line, to_report_records
from gpr_analyzer.io import read as read_file
from gpr_analyzer.processing import ProcessingOptions, ProcessingResult, run_pipeline
from gpr_analyzer.report import write_report
from gpr_analyzer.report.rules import suggest_action
from gpr_analyzer.report.schema import (ACTION_LEVELS, CONFIDENCE_LEVELS, SURFACE_USES, ZONE_TYPES, ArtifactRegion,
                                        Evidence, LineResult, ProcessingStep, SurveyInfo, SurveyResult, Zone)

APP_VERSION = "0.1"
VIEWS = ("grey", "high_gain", "envelope", "viewpoint", "autodisplay")
EDITABLE = ("trace_start", "trace_end", "time_top_ns", "time_bottom_ns", "suspected_type", "confidence", "action",
            "note")
# All processing fields must survive imported workflows and checkpoints.
OPTION_FIELDS = tuple(f.name for f in fields(ProcessingOptions))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_options(d: dict[str, Any] | None) -> ProcessingOptions:
    """ProcessingOptions from the form; blank values mean 'automatic'."""
    from gpr_analyzer.processing.workflows import options_from_dict

    if d is not None and not isinstance(d, dict):
        raise ValueError("options must be an object")
    clean = {}
    strings = {"background", "background_statistic", "time_zero_source", "migration"}
    for key, value in (d or {}).items():
        if value in (None, "", []):
            continue
        if key not in OPTION_FIELDS:
            raise ValueError(f"unknown processing option: {key}")
        if key == "band_mhz":
            try:
                value = [float(x) for x in value]
            except (TypeError, ValueError):
                raise ValueError("band_mhz needs [low, high]") from None
        elif key not in strings | {"time_zero", "estimate_velocity"}:
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise ValueError(f"invalid {key}") from None
        clean[key] = value
    return options_from_dict(clean)


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
    trace_offset: int = 0  # first trace of the processed range in the file (0 = whole line)
    # reviewer decisions about the line as a whole
    sections: list[dict[str, Any]] = field(default_factory=list)  # trace ranges whose results are not used
    status: str = "pending"  # pending | approved | redo (see LINE_STATUS)
    status_note: str = ""
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)  # PNGs and GPS positions
    revision: str = field(default_factory=lambda: uuid.uuid4().hex)

    # ------------------------------------------------------------------ processing
    def process(self, progress: Callable[[str], None] | None = None) -> None:
        res = run_pipeline(self.raw, self.options, progress=progress, tracks={"analysis", "envelope", "display", "viewpoint", "autodisplay"})
        if progress:
            progress("detect")
        det = analyze_line(res.analysis, velocity_m_per_ns=res.velocity_m_per_ns,
                           velocity_source=res.velocity_source_code)
        zones, regions = to_report_records(det, line_id=self.line_id)
        self.result = res
        off = self.offset_m
        self.zones = [Zone.from_dict({**z, **({"distance_start_m": z["distance_start_m"] + off,
                                               "distance_end_m": z["distance_end_m"] + off}
                                            if off and z.get("distance_start_m") is not None else {})}) for z in zones]
        self.artifact_regions = [ArtifactRegion(**a) for a in regions]
        self.auto_zones = {z.zone_id: z.to_dict() for z in self.zones}
        self._cache.clear()
        self.revision = uuid.uuid4().hex

    @property
    def offset_m(self) -> float:
        """Distance of the first processed trace along the whole line (distances in zones are absolute)."""
        return self.trace_offset * (self.raw.dx_m or 0.0)

    @property
    def analysis(self) -> Radargram:
        assert self.result is not None
        return self.result.analysis

    def excluded(self, z: Zone) -> str | None:
        """Id of the excluded section holding the zone's centre, or None."""
        c = (z.trace_start + z.trace_end) / 2
        return next((s["section_id"] for s in self.sections if s["trace_start"] <= c <= s["trace_end"]), None)

    def zone(self, zone_id: str) -> Zone:
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        raise KeyError(zone_id)

    # ------------------------------------------------------------------ images
    def views(self) -> list[str]:
        return [v for v in VIEWS if v not in ("viewpoint", "autodisplay") or getattr(self.result, v) is not None]

    def positions(self) -> tuple[np.ndarray, np.ndarray] | None:
        """(lat, lon) per trace from the .cor GPS, or None."""
        if "positions" not in self._cache:
            self._cache["positions"] = geo.trace_positions(self.raw.metadata.get("gps"), self.analysis.n_traces)
        return self._cache["positions"]

    def with_coords(self, z: Zone) -> Zone:
        """Zone with WGS84 start/end coordinates from the GPS track, when the line has one."""
        pos = self.positions()
        if pos is None or z.coord_start is not None:
            return z
        lat, lon = pos
        n = len(lat) - 1
        i0, i1 = min(max(z.trace_start, 0), n), min(max(z.trace_end, 0), n)
        out = _replace(z, coord_start=(float(lon[i0]), float(lat[i0])), coord_end=(float(lon[i1]), float(lat[i1])),
                       datum="WGS84")
        # Altitude is needed for Condor's GPS Z. Never substitute depth or zero for a missing altitude.
        heights = {}
        for g in self.raw.metadata.get("gps") or []:
            try:
                tr, height = float(g["trace"]), float(g["elevation"])
                if np.isfinite([tr, height]).all() and g.get("quality") != 0:
                    heights.setdefault(tr, height)
            except (KeyError, TypeError, ValueError):
                continue
        if len(heights) >= 2:
            traces = sorted(heights)
            if traces[0] <= i0 <= i1 <= traces[-1]:
                out = _replace(out, surface_elevation_start_m=float(np.interp(i0, traces, [heights[t] for t in traces])),
                               surface_elevation_end_m=float(np.interp(i1, traces, [heights[t] for t in traces])))
        return out

    def png(self, view: str) -> bytes:
        if view not in self.views():
            raise KeyError(view)
        if view not in self._cache:
            self._cache[view] = render_png(self.result, view)
        return self._cache[view]

    def line_result(self) -> LineResult:
        res = self.result
        return LineResult(
            line_id=self.line_id, source_file=self.source_file + (
                f" ({self.offset_m:.1f}–{self.offset_m + res.analysis.n_traces * (res.analysis.dx_m or 0):.1f} m)"
                if self.trace_offset else ""), n_traces=res.analysis.n_traces,
            n_samples=res.analysis.n_samples, dt_ns=res.analysis.dt_ns, trace_spacing_m=res.analysis.dx_m,
            velocity_m_per_ns=res.velocity_m_per_ns, velocity_source=res.velocity_source_code,
            processing=[ProcessingStep(**s) for s in res.report_steps()],
            zones=[self.with_coords(z) for z in self.zones if not self.excluded(z)],
            artifact_regions=list(self.artifact_regions) + [
                ArtifactRegion(trace_start=s["trace_start"], trace_end=s["trace_end"], reason="excluded_by_reviewer")
                for s in self.sections], radargram=res.analysis,
            figure_cache=self._cache.setdefault("report_figures", {}))

    def to_json(self, surface_use: str) -> dict[str, Any]:
        res = self.result
        a = res.analysis
        zones = []
        for z in self.zones:
            d = z.to_dict()
            d["suggested_action"] = suggest_action(z, surface_use)
            d["manual"] = z.zone_id not in self.auto_zones
            d["excluded"] = self.excluded(z)
            zones.append(d)
        return {
            "line_id": self.line_id, "revision": self.revision, "source_file": self.source_file, "channel": self.channel,
            "n_traces": a.n_traces, "n_samples": a.n_samples, "dt_ns": a.dt_ns, "dx_m": a.dx_m,
            "time_window_ns": a.n_samples * a.dt_ns, "views": self.views(),
            "trace_offset": self.trace_offset, "crop": self.raw.metadata.get("crop"),
            "has_gps": self.positions() is not None,
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
            "sections": self.sections, "status": self.status, "status_note": self.status_note,
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
    elif view == "viewpoint":
        n = res.analysis.n_samples  # same time zero as the analysis track; match its rows so boxes line up
        d = res.viewpoint.data[:n]
        if d.shape[0] < n:
            d = np.vstack([d, np.zeros((n - d.shape[0], d.shape[1]), dtype=d.dtype)])
        c = float(np.percentile(np.abs(d), 98.0)) or 1.0
        img, cmap, lo, hi = d, "gray", -c, c
    elif view == "autodisplay":
        d = res.autodisplay.data
        c = res.autodisplay.metadata["display_clip"]
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
    source_folder: str = ""  # survey read in place from this folder ("" = files uploaded into uploads/)
    plan: list[dict[str, Any]] = field(default_factory=list)
    progress: dict[str, Any] = field(default_factory=lambda: {"state": "done"})
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _log_tail: list[str] = field(default_factory=list, repr=False)
    _pending_lines: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

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

    # ------------------------------------------------------------------ loading and processing
    # A job first gets a plan (one entry per data file, with an optional distance range), then the plan is
    # processed line by line, in a background thread in the app. Every finished line is written to
    # checkpoints/<line>.pkl and state.json, and each stage goes to log.txt, so a job cut off by a power
    # failure or crash reopens with the finished lines and continues with the rest (resume()).
    def load(self, paths: list[Path], ranges: dict[str, list[float]] | None = None,
             include: set[str] | None = None) -> None:
        """Plan and process synchronously (tests, old jobs). The app uses plan_files() + start()."""
        self.plan_files(paths, ranges, include)
        self.run_plan()

    def plan_files(self, paths: list[Path], ranges: dict[str, list[float]] | None = None,
                   include: set[str] | None = None) -> None:
        """ranges / include are keyed by the primary file's full path (as scan_folder reports it)."""
        s = sort_files(paths)
        groups = sorted(s.groups, key=lambda g: str(g.primary).lower())
        if include is not None:
            groups = [g for g in groups if str(g.primary) in include]
        self.plan = []
        for g in groups:
            r = (ranges or {}).get(str(g.primary))
            if r is not None:
                lo, hi = (None if v in (None, "") else float(v) for v in r)
                r = None if lo is None and hi is None else [lo, hi]
            self.plan.append({"index": len(self.plan), "primary": str(g.primary), "name": g.primary.name,
                              "family": g.family, "companions": [str(c) for c in g.companions], "range_m": r,
                              "state": "waiting", "step": "", "line_ids": [], "error": None})
        self.unassigned = [_companion(p) for p in s.unassigned]
        self.problems = [{"file": p.name, "error": "unknown_format"} for p in s.unassigned
                         if p.suffix.lower() not in {".txt", ".csv", ".cor", ".gps", ".xml", ".ini", ".json", ".log",
                                                      ".mrk", ".time", ".tmf"}]
        if not self.plan and not self.problems:
            self.problems.append({"file": "", "error": "no_files"})
        self.progress = {"state": "waiting", "started": None, "finished": None}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Process the plan entries that are not done yet, in a background thread."""
        if self.running:
            return
        self._thread = threading.Thread(target=self.run_plan, name=f"job-{self.job_id}", daemon=True)
        self._thread.start()

    def run_plan(self) -> None:
        todo = [e for e in self.plan if e["state"] != "done"]
        resumed = len(todo) < len(self.plan)
        with self.lock:
            self.progress.update(state="processing", started=_now(), finished=None)
            self.save()
        self.log(("ทำต่อ: เหลือ " if resumed else "เริ่มประมวลผล ") + f"{len(todo)} จาก {len(self.plan)} ไฟล์"
                 + (f" (อ่านจากโฟลเดอร์ {self.source_folder})" if self.source_folder else ""))
        t0 = time.monotonic()
        for e in todo:
            self._process_entry(e)
        with self.lock:
            self.progress.update(state="done", finished=_now())
            if not self.lines and not self.problems:
                self.problems.append({"file": "", "error": "no_files"})
            self.save()
        n_err = sum(e["state"] == "error" for e in self.plan)
        self.log(f"เสร็จ: {len(self.lines)} เส้น ใช้เวลา {time.monotonic() - t0:.0f} วินาที"
                 + (f" · อ่านไม่ได้ {n_err} ไฟล์" if n_err else ""))

    def _step(self, e: dict[str, Any], step: str) -> None:
        e["step"] = step
        e["steps_done"] = e.get("steps_done", 0) + 1

    def _process_entry(self, e: dict[str, Any]) -> None:
        primary = Path(e["primary"])
        e.update(state="reading", step="read", steps_done=0, error=None)
        self.log(f"[{e['index'] + 1}/{len(self.plan)}] {e['name']}: อ่านไฟล์")
        try:
            rgs = read_file(primary, range_m=e["range_m"]) if e["range_m"] else read_file(primary)
        except Exception as ex:  # noqa: BLE001 - reported to the user per file
            self._entry_failed(e, f"{type(ex).__name__}: {ex}")
            return
        comps = [_companion(Path(c)) for c in e["companions"] if Path(c).exists()]
        ids = []
        for ch, rg in enumerate(rgs):
            line_id = f"L{e['index'] + 1}" if len(rgs) == 1 else f"L{e['index'] + 1}-ch{ch + 1}"
            offset = rg.metadata.get("crop", {}).get("trace_start", 0)
            ln = LineState(line_id=line_id, source_file=primary.name, channel=ch + 1 if len(rgs) > 1 else None,
                           raw=rg, companions=comps, trace_offset=offset)
            saved = self._pending_lines.get(line_id)
            if saved and saved.get("source_file") == ln.source_file:
                ln.options = parse_options(saved.get("options"))
            e.update(state="processing")
            try:
                ln.process(progress=lambda step: self._step(e, step))
                if saved and saved.get("source_file") == ln.source_file:
                    carry_reviews(ln, [Zone.from_dict(z) for z in saved.get("zones", [])], set(saved.get("auto_zones", {})))
                    ln.sections = saved.get("sections", [])
                    ln.status, ln.status_note = saved.get("status", "pending"), saved.get("status_note", "")
            except Exception as ex:  # noqa: BLE001
                self._entry_failed(e, f"processing failed: {type(ex).__name__}: {ex}")
                return
            e.update(state="saving", step="checkpoint")
            self.write_checkpoint(ln, e)
            with self.lock:
                self.lines = sorted([x for x in self.lines if x.line_id != line_id] + [ln], key=_line_order)
                self._pending_lines.pop(line_id, None)
            ids.append(line_id)
            self.log(line_summary(ln))
        with self.lock:
            e.update(state="done", step="", line_ids=ids)
            self.save()

    def _entry_failed(self, e: dict[str, Any], msg: str) -> None:
        with self.lock:
            e.update(state="error", error=msg)
            self.problems.append({"file": e["name"], "error": msg})
            self.save()
        self.log(f"{e['name']}: ผิดพลาด: {msg}")

    def log(self, text: str) -> None:
        """One line of progress to log.txt (open it with Notepad while the job runs) and to the page."""
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {text}"
        self._log_tail.append(line)
        del self._log_tail[:-300]
        with (self.folder / "log.txt").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def checkpoint_path(self, line_id: str) -> Path:
        return self.folder / "checkpoints" / f"{line_id}.pkl"

    def write_checkpoint(self, ln: LineState, e: dict[str, Any]) -> None:
        """Processed line (all display tracks, automatic zones) so reopening needs no reprocessing."""
        d = self.checkpoint_path(ln.line_id)
        d.parent.mkdir(exist_ok=True)
        res = ln.result
        slim = ProcessingResult(**{**{f.name: getattr(res, f.name) for f in fields(res)}, "layers": None, "energy": None,
                                   "log": list(res.log)})
        rec = {"version": CHECKPOINT_VERSION, "app_version": APP_VERSION, "processing_revision": PROCESSING_REVISION, "source": e["primary"],
               "source_stamp": _stamp(Path(e["primary"])), "range_m": e["range_m"],
               "options": options_dict(ln.options),
               "line": {"line_id": ln.line_id, "source_file": ln.source_file, "channel": ln.channel, "raw": ln.raw,
                        "companions": ln.companions, "options": ln.options, "result": slim,
                        "zones": [z.to_dict() for z in ln.zones], "auto_zones": ln.auto_zones,
                        "artifact_regions": [asdict(r) for r in ln.artifact_regions], "trace_offset": ln.trace_offset}}
        tmp = d.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump(rec, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(d)

    def read_checkpoint(self, e: dict[str, Any], line_id: str) -> LineState | None:
        """The saved line for the same source dependencies, range and processing revision; else None."""
        p = self.checkpoint_path(line_id)
        if not p.is_file():
            return None
        try:
            with p.open("rb") as f:
                rec = pickle.load(f)  # our own file, written by write_checkpoint
        except Exception:  # noqa: BLE001 - a half-written or old file is simply redone
            return None
        if (rec.get("version") != CHECKPOINT_VERSION or rec.get("processing_revision") != PROCESSING_REVISION
                or rec.get("source") != e["primary"]
                or rec.get("range_m") != e["range_m"] or rec.get("source_stamp") != _stamp(Path(e["primary"]))):
            return None
        d = rec["line"]
        ln = LineState(line_id=d["line_id"], source_file=d["source_file"], channel=d["channel"], raw=d["raw"],
                       companions=d["companions"], options=d["options"], result=d["result"],
                       zones=[Zone.from_dict(z) for z in d["zones"]], auto_zones=d["auto_zones"],
                       artifact_regions=[ArtifactRegion(**a) for a in d["artifact_regions"]],
                       trace_offset=d["trace_offset"])
        return ln

    def progress_json(self) -> dict[str, Any]:
        total = len(self.plan)
        frac = 0.0
        for e in self.plan:
            if e["state"] in ("done", "error"):
                frac += 1
            elif e["state"] != "waiting":
                frac += min(e.get("steps_done", 0) / STEPS_PER_LINE, 0.95)
        state = self.progress.get("state", "done")
        if state == "processing" and not self.running and any(e["state"] not in ("done", "error") for e in self.plan):
            state = "interrupted"
        return {**self.progress, "state": state, "running": self.running, "total": total,
                "done": sum(e["state"] == "done" for e in self.plan), "fraction": round(frac / total, 3) if total else 1.0,
                "entries": [{k: e.get(k) for k in ("index", "name", "state", "step", "line_ids", "error", "range_m")}
                            for e in self.plan],
                "log": self._log_tail[-60:]}

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
                "antenna_separation_m": ln.analysis.metadata.get("antenna_separation_m") or 0.0,
                "velocity_source": res.velocity_source_code, "suspected_type": "unknown", "confidence": "low",
                "review": "edited", "evidence": [{"kind": "present", "code": "manual",
                                                  "text": "วาดเพิ่มโดยผู้ตรวจทาน",
                                                  "params": {"text_en": "drawn by the reviewer"}}]}
        base.update(_clean_changes(values, ln))
        for key in ("trace_start", "trace_end", "time_top_ns", "time_bottom_ns"):
            if key not in base:
                raise ValueError(f"{key} is required")
        if ln.analysis.dx_m is not None:
            base["distance_start_m"] = (base["trace_start"] + ln.trace_offset) * ln.analysis.dx_m
            base["distance_end_m"] = (base["trace_end"] + ln.trace_offset) * ln.analysis.dx_m
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
        old, old_auto = list(ln.zones), set(ln.auto_zones)
        previous = ln.options
        ln.options = opt
        try:
            ln.process()
        except Exception:
            ln.options = previous
            raise
        carried, dropped = carry_reviews(ln, old, old_auto)
        if ln.status == "redo":
            ln.status = "pending"
        e = next((e for e in self.plan if ln.line_id in e["line_ids"]), None)
        if e is not None:
            self.write_checkpoint(ln, e)
        self.log(f"ประมวลผลใหม่ {ln.line_id} ด้วยค่าที่ผู้ใช้ตั้ง {options_dict(opt) or '(อัตโนมัติ)'}")
        self.log(line_summary(ln))
        self._label("reprocess", ln, None, {"reviewed_zones_discarded": dropped},
                    {"options": options_dict(opt), "reviews_carried": carried})
        return ln

    # ------------------------------------------------------------------ line-level review
    def add_section(self, line_id: str, d: dict[str, Any]) -> dict[str, Any]:
        """Do not use the results between two traces (bad data, obstacle, outside the survey area...)."""
        ln = self.line(line_id)
        n = ln.analysis.n_traces
        t0, t1 = sorted(int(min(max(round(float(d[k])), 0), n - 1)) for k in ("trace_start", "trace_end"))
        if t1 - t0 < 1:
            raise ValueError("the section must cover at least two traces")
        used = {s["section_id"] for s in ln.sections}
        k = 1
        while f"S{k}" in used:
            k += 1
        dx = ln.analysis.dx_m
        sec = {"section_id": f"S{k}", "trace_start": t0, "trace_end": t1, "reason": str(d.get("reason", ""))[:300],
               "distance_start_m": round((t0 + ln.trace_offset) * dx, 2) if dx else None,
               "distance_end_m": round((t1 + ln.trace_offset) * dx, 2) if dx else None}
        ln.sections.append(sec)
        self._label("exclude_section", ln, None, None, sec)
        self.log(f"{ln.line_id}: ไม่ใช้ผลช่วง {sec['section_id']} trace {t0}–{t1}" + (f" ({sec['reason']})" if sec["reason"] else ""))
        return sec

    def delete_section(self, line_id: str, section_id: str) -> None:
        ln = self.line(line_id)
        sec = next((s for s in ln.sections if s["section_id"] == section_id), None)
        if sec is None:
            raise KeyError(section_id)
        ln.sections.remove(sec)
        self._label("restore_section", ln, None, sec, None)

    def set_line_status(self, line_id: str, status: str, note: str | None = None) -> None:
        ln = self.line(line_id)
        if status not in LINE_STATUS:
            raise ValueError(f"status must be one of {', '.join(LINE_STATUS)}")
        before = {"status": ln.status, "note": ln.status_note}
        ln.status = status
        if note is not None:
            ln.status_note = str(note)[:500]
        self._label("line_status", ln, None, before, {"status": ln.status, "note": ln.status_note})

    def summary(self) -> dict[str, Any]:
        """Key facts to check before the report: per line, the findings that need action, and warnings."""
        su = self.info.surface_use
        lines, findings, warn = [], [], []
        for ln in self.lines:
            a, res = ln.analysis, ln.result
            used = [z for z in ln.zones if not ln.excluded(z)]
            by_type: dict[str, int] = {}
            for z in used:
                if z.review != "rejected":
                    by_type[z.suspected_type] = by_type.get(z.suspected_type, 0) + 1
            rc = review_counts(used)
            length = a.n_traces * a.dx_m if a.dx_m else None
            lines.append({
                "line_id": ln.line_id, "source_file": ln.source_file, "crop": ln.raw.metadata.get("crop"),
                "length_m": round(length, 2) if length else None, "has_gps": ln.positions() is not None,
                "velocity_m_per_ns": res.velocity_m_per_ns, "velocity_source": res.velocity_source_code,
                "options": options_dict(ln.options), "zones": len(used), "by_type": by_type, "review": rc,
                "excluded_zones": len(ln.zones) - len(used), "sections": ln.sections,
                "status": ln.status, "status_note": ln.status_note})
            for z in used:
                act = suggest_action(z, su)
                if z.review != "rejected" and act in ("E", "1", "2"):
                    zz = ln.with_coords(z)
                    findings.append({"line_id": ln.line_id, "zone_id": z.zone_id, "suspected_type": z.suspected_type,
                                     "confidence": z.confidence, "action": act, "review": z.review,
                                     "distance_start_m": z.distance_start_m, "distance_end_m": z.distance_end_m,
                                     "depth_top_m": round(z.depth_top_m, 2), "depth_bottom_m": round(z.depth_bottom_m, 2),
                                     "coord": list(zz.coord_start) if zz.coord_start else None})
            if rc["auto"]:
                warn.append({"line_id": ln.line_id, "code": "unreviewed", "n": rc["auto"]})
            if res.velocity_source_code == "default":
                warn.append({"line_id": ln.line_id, "code": "default_velocity"})
            if ln.positions() is None:
                warn.append({"line_id": ln.line_id, "code": "no_gps"})
            if ln.status != "approved":
                warn.append({"line_id": ln.line_id, "code": "redo" if ln.status == "redo" else "not_approved"})
        order = {"E": 0, "1": 1, "2": 2}
        findings.sort(key=lambda f: (order[f["action"]], f["line_id"], f["zone_id"]))
        pr = self.progress_json()
        if pr["state"] != "done":
            warn.insert(0, {"line_id": None, "code": "processing_" + pr["state"], "n": pr["total"] - pr["done"]})
        for f in ("project", "site", "survey_date"):
            if not getattr(self.info, f):
                warn.append({"line_id": None, "code": "missing_info", "field": f})
        if self.problems:
            warn.append({"line_id": None, "code": "unreadable_files", "n": len(self.problems)})
        zs = [z for ln in self.lines for z in ln.zones if not ln.excluded(z)]
        return {"info": asdict(self.info), "lang": self.lang, "reviewer": self.reviewer, "lines": lines,
                "findings": findings, "warnings": warn, "ready": not warn,
                "totals": {"lines": len(self.lines), "approved": sum(ln.status == "approved" for ln in self.lines),
                           "length_m": round(sum(x["length_m"] or 0 for x in lines), 1),
                           "zones": len(zs), "review": review_counts(zs),
                           "sections": sum(len(ln.sections) for ln in self.lines)}}

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
            "version": 2, "job_id": self.job_id, "created": self.created, "saved": _now(), "info": asdict(self.info),
            "lang": self.lang, "reviewer": self.reviewer, "source_folder": self.source_folder,
            "plan": self.plan, "progress": self.progress, "problems": self.problems, "unassigned": self.unassigned,
            "lines": [{"line_id": ln.line_id, "source_file": ln.source_file, "options": options_dict(ln.options),
                       "zones": [z.to_dict() for z in ln.zones], "auto_zones": ln.auto_zones, "sections": ln.sections,
                       "status": ln.status, "status_note": ln.status_note} for ln in self.lines],
            "pending_lines": self._pending_lines,
            "comparison": self.comparison,
            # read by the job list and the all-jobs comparison export without reprocessing
            "summary": {"n_lines": len(self.lines), "files": sorted({ln.source_file for ln in self.lines}),
                        "n_planned": len(self.plan), "n_done": sum(e["state"] == "done" for e in self.plan),
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
        """Reopen a job saved by an earlier run.

        Lines come back from their checkpoints; a line without one (the run was cut off, or the job is from
        an older version) is processed again: synchronously for old jobs, by resume() for unfinished ones.
        Then the saved options, zones and reviews are put back.
        """
        st = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        job = cls(job_id=st["job_id"], folder=folder, created=st.get("created", _now()))
        job.info = SurveyInfo(**{k: v for k, v in st.get("info", {}).items() if k in {f.name for f in fields(SurveyInfo)}})
        job.lang, job.reviewer = st.get("lang", "bi"), st.get("reviewer", "")
        job.comparison = cmp.upgrade_state(st.get("comparison") or cmp.new_state())
        job.source_folder = st.get("source_folder", "")
        if "plan" not in st:  # version 1: no plan or checkpoints yet
            job.load(sorted(p for p in job.upload_dir.iterdir() if p.is_file()))
        else:
            job.plan, job.progress = st["plan"], st.get("progress", {"state": "done"})
            job.problems, job.unassigned = st.get("problems", []), st.get("unassigned", [])
            for e in job.plan:
                if e["state"] != "done":
                    if e["state"] != "error":
                        e.update(state="waiting", step="")
                    continue
                lines = [job.read_checkpoint(e, lid) for lid in e["line_ids"]]
                if all(lines):
                    job.lines.extend(lines)
                else:
                    e.update(state="waiting", step="", line_ids=[])  # checkpoint missing or stale: redo
            job.lines.sort(key=_line_order)
            pending = [e for e in job.plan if e["state"] == "waiting"]
            if pending and job.progress.get("state") == "done":
                job.progress["state"] = "processing"  # shown as "interrupted" until resumed
            if pending:
                job.log(f"เปิดงานอีกครั้ง: เสร็จแล้ว {len(job.plan) - len(pending)} ไฟล์ ค้าง {len(pending)} ไฟล์ (กด 'ทำต่อ')")
        saved = {d["line_id"]: d for d in st.get("lines", [])}
        saved = {**st.get("pending_lines", {}), **saved}
        loaded = {ln.line_id for ln in job.lines}
        job._pending_lines = {lid: d for lid, d in saved.items() if lid not in loaded}
        for ln in job.lines:
            d = saved.get(ln.line_id)
            if not d or d.get("source_file") != ln.source_file:
                continue
            if d.get("options") and d["options"] != options_dict(ln.options):
                ln.options = parse_options(d["options"])
                ln.process()
            ln.zones = [Zone.from_dict(z) for z in d.get("zones", [])]
            ln.auto_zones = d.get("auto_zones", ln.auto_zones)
            ln.sections = d.get("sections", [])
            ln.status, ln.status_note = d.get("status", "pending"), d.get("status_note", "")
        return job

    def _reset_failed(self) -> bool:
        if not any(e["state"] != "done" for e in self.plan) or self.running:
            return False
        for e in self.plan:
            if e["state"] == "error":
                e.update(state="waiting", error=None)
        self.problems = [p for p in self.problems if p.get("error") == "unknown_format"]
        return True

    def resume(self) -> None:
        """Continue an interrupted job (and retry files that failed) in the background."""
        if self._reset_failed():
            self.start()

    def resume_now(self) -> None:
        if self._reset_failed():
            self.run_plan()

    def export(self, name: str) -> Path:
        """Write report.pdf/.docx/.html, zones.csv or survey.json into the job folder."""
        out = self.folder / "exports"
        out.mkdir(exist_ok=True)
        allowed = {"report.pdf", "report.docx", "report.html", "zones.csv", "survey.json",
                   "zones.cnf", "zones.pobj", "zones.lobj", "zones-condor.zip"}
        if name not in allowed:
            raise KeyError(name)
        if name == "zones-condor.zip":
            from gpr_analyzer.report.exports import condor_zip
            return condor_zip(self.survey(), out / name)
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
            "source_folder": self.source_folder, "progress": self.progress_json(),
            "reviewer": self.reviewer, "problems": self.problems, "unassigned": self.unassigned,
            "review": review_counts(zones),
            "draft": any(z.review == "auto" and not ln.excluded(z) for ln in self.lines for z in ln.zones),
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
                            velocity_m_per_ns=ln.result.velocity_m_per_ns, offset_traces=ln.trace_offset,
                            antenna_separation_m=a.metadata.get("antenna_separation_m") or 0.0)

    def comparison_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for ln in self.lines:
            ls = cmp.line_state(self.comparison, ln.line_id)
            zs, geom = [z.to_dict() for z in ln.zones], self.geom(ln)
            key = json.dumps([ls, zs, asdict(geom), self.comparison["tolerance"]], sort_keys=True, default=str)
            cached = ln._cache.get("comparison")
            if cached is None or cached[0] != key:
                cached = (key, cmp.compare_line(ls, zs, geom, self.comparison["tolerance"]))
                ln._cache["comparison"] = cached
            res = cached[1]
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


LINE_STATUS = ("pending", "approved", "redo")
CHECKPOINT_VERSION = 1
PROCESSING_REVISION = 2  # depth/reference semantics; independent of UI or package releases


def _iou(a: Zone, b: Zone) -> float:
    """Overlap of two zone boxes in (trace, time), 0..1."""
    w = min(a.trace_end, b.trace_end) - max(a.trace_start, b.trace_start) + 1
    h = min(a.time_bottom_ns, b.time_bottom_ns) - max(a.time_top_ns, b.time_top_ns)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    area = lambda z: (z.trace_end - z.trace_start + 1) * max(z.time_bottom_ns - z.time_top_ns, 1e-9)  # noqa: E731
    return inter / (area(a) + area(b) - inter)


def carry_reviews(ln: LineState, old: list[Zone], old_auto: set[str], min_iou: float = 0.5
                  ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """After reprocessing, keep the reviewer's work: zones they drew stay, and a decision on an old automatic zone
    moves to the new zone that covers the same place (box overlap >= min_iou). Returns (carried, dropped)."""
    carried, dropped, taken = [], [], set()
    for oz in old:
        if oz.zone_id not in old_auto:  # drawn by the reviewer
            ln.zones.append(_replace(oz, velocity_m_per_ns=ln.result.velocity_m_per_ns,
                                     velocity_source=ln.result.velocity_source_code,
                                     antenna_separation_m=ln.analysis.metadata.get("antenna_separation_m") or 0.0))
            carried.append({"from": oz.zone_id, "to": oz.zone_id})
            continue
        if oz.review == "auto":
            continue
        cands = [(i, _iou(oz, nz)) for i, nz in enumerate(ln.zones) if nz.zone_id in ln.auto_zones and i not in taken]
        best = max(cands, key=lambda c: c[1], default=(None, 0.0))
        if best[0] is None or best[1] < min_iou:
            dropped.append(oz.to_dict())
            continue
        i = best[0]
        nz = ln.zones[i]
        keep = {"review": oz.review, "note": oz.note, "ground_truth": oz.ground_truth}
        if oz.ground_truth:
            keep["confidence"] = oz.confidence
        if oz.review == "edited":
            keep.update({k: getattr(oz, k) for k in ("trace_start", "trace_end", "time_top_ns", "time_bottom_ns",
                                                      "distance_start_m", "distance_end_m", "suspected_type",
                                                      "confidence", "action")})
        ln.zones[i] = _replace(nz, **keep)
        taken.add(i)
        carried.append({"from": oz.zone_id, "to": nz.zone_id})
    return carried, dropped
STEPS_PER_LINE = 15  # run_pipeline steps + detection, for the progress bar


def _stamp(p: Path) -> list[dict[str, Any]]:
    """Data/header plus channel-specific and shared navigation dependencies, with their names."""
    out = []
    stems = {p.stem.lower()}
    if p.stem.endswith(("_0", "_1")):
        stems.add(p.stem[:-2].lower())
    for q in sorted(p.parent.iterdir()):
        if q.stem.lower() in stems and q.suffix.lower() in (
                ".iprh", ".iprb", ".rad", ".rd3", ".rd7", ".hd", ".dt1", ".dzt", ".sgy", ".segy", ".cor", ".mrk", ".time"):
            st = q.stat()
            out.append({"name": q.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return out


def _line_order(ln: LineState) -> tuple[int, str]:
    head = ln.line_id[1:].split("-")[0]
    return (int(head) if head.isdigit() else 10**9, ln.line_id)


STEP_TH = {"read": "อ่านไฟล์", "center_frequency": "หาความถี่", "dewow": "Dewow", "time_zero": "Time-zero",
           "bandpass": "Bandpass", "background_removal": "ลบพื้นหลัง", "gain": "Gain", "velocity": "หาความเร็วคลื่น",
           "migration": "Migration", "envelope": "Envelope", "agc": "AGC", "viewpoint_display": "ภาพแบบ ViewPoint",
           "display_qc": "ตรวจ trace (แสดงผล)", "display_bandpass": "Bandpass อัตโนมัติ (แสดงผล)",
           "display_background": "ลบพื้นหลังอัตโนมัติ (แสดงผล)", "display_gain": "Gain อัตโนมัติ (แสดงผล)",
           "display_contrast": "Contrast อัตโนมัติ (แสดงผล)", "detect": "ค้นหาโซนผิดปกติ", "checkpoint": "บันทึก checkpoint"}
TYPE_TH = {"void_like": "คล้ายโพรง", "pipe_point": "ท่อ/วัตถุจุด", "layer": "ชั้นดิน", "scattered": "สะท้อนกระจัดกระจาย",
           "artifact": "artifact", "unknown": "ไม่ระบุ"}
CONF_TH = {"high": "สูง", "medium": "กลาง", "low": "ต่ำ", "confirmed": "ยืนยัน"}


def line_summary(ln: LineState) -> str:
    """What one processed line gave, for log.txt: size, velocity, zone counts and the suspicious ones."""
    a, res = ln.analysis, ln.result
    length = f"{a.n_traces * a.dx_m:.1f} m" if a.dx_m else "ไม่ทราบระยะ"
    rng = f" ช่วง {ln.offset_m:.1f}–{ln.offset_m + a.n_traces * (a.dx_m or 0):.1f} m" if ln.trace_offset else ""
    counts: dict[str, int] = {}
    for z in ln.zones:
        counts[z.suspected_type] = counts.get(z.suspected_type, 0) + 1
    txt = (f"{ln.line_id} {ln.source_file}{rng}: {a.n_traces} trace, {length}, v {res.velocity_m_per_ns:.3f} m/ns "
           f"({res.velocity_source_code}) → {len(ln.zones)} โซน"
           + (": " + ", ".join(f"{TYPE_TH.get(k, k)} {v}" for k, v in sorted(counts.items())) if counts else ""))
    for z in ln.zones:
        if z.suspected_type == "void_like" and z.confidence in ("high", "medium"):
            where = (f"{z.distance_start_m:.1f}–{z.distance_end_m:.1f} m" if z.distance_start_m is not None
                     else f"trace {z.trace_start}–{z.trace_end}")
            txt += (f"\n    ⚠ น่าสงสัย {z.zone_id}: {TYPE_TH[z.suspected_type]} เชื่อมั่น{CONF_TH[z.confidence]} ที่ {where}"
                    f" ลึก {z.depth_top_m:.2f}–{z.depth_bottom_m:.2f} m")
    return txt


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
            out["distance_start_m"] = (out["trace_start"] + ln.trace_offset) * a.dx_m
        if "trace_end" in out:
            out["distance_end_m"] = (out["trace_end"] + ln.trace_offset) * a.dx_m
    return out


class JobStore:
    """Jobs in memory; files, survey.json and labels.jsonl on disk under `root`."""

    def __init__(self, root: Path, max_cached_jobs: int = 3) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: WeakValueDictionary[str, Job] = WeakValueDictionary()
        self._recent: OrderedDict[str, Job] = OrderedDict()
        self._max_cached_jobs = max(1, max_cached_jobs)
        self._lock = threading.RLock()

    def _remember(self, job: Job) -> Job:
        self.jobs[job.job_id] = job
        self._recent[job.job_id] = job
        self._recent.move_to_end(job.job_id)
        while len(self._recent) > self._max_cached_jobs:
            self._recent.popitem(last=False)
        # Active requests and worker threads keep their own strong reference. The weak index
        # reuses that exact object even after LRU eviction, so concurrent edits cannot fork it.
        return job

    def new(self) -> Job:
        jid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job = Job(job_id=jid, folder=self.root / jid)
        job.upload_dir.mkdir(parents=True)
        with self._lock:
            return self._remember(job)

    def get(self, job_id: str) -> Job:
        with self._lock:
            cached = self.jobs.get(job_id)
            if cached is not None:
                return self._remember(cached)
            folder = self.root / job_id
            if "/" in job_id or "\\" in job_id or job_id.startswith(".") or not (folder / "state.json").is_file():
                raise KeyError(job_id)
            return self._remember(Job.restore(folder))

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
        def state(s: dict[str, Any]) -> str:
            j = self.jobs.get(s["job_id"])
            if j is not None:
                return j.progress_json()["state"]
            st = s.get("progress", {}).get("state", "done")
            return "interrupted" if st in ("processing", "waiting") else st
        return [{"job_id": s["job_id"], "created": s.get("created"), "saved": s.get("saved"), "state": state(s),
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
