"""Turn a SurveyResult into a language-specific list of blocks.

All renderers (PDF, Word, HTML) draw the same blocks, so the report content is decided once here
and the renderers only handle layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from gpr_analyzer.core import depth_from_time

from gpr_analyzer.report.i18n import Translator
from gpr_analyzer.report.rules import action_rank, suggest_action
from gpr_analyzer.report.schema import ACTION_LEVELS, LineResult, SurveyResult, Zone

EVIDENCE_MARK = {"present": "✓", "absent": "✗", "warning": "⚠", "not_assessed": "–"}


@dataclass
class Heading:
    text: str
    level: int = 1


@dataclass
class Para:
    text: str
    style: str = "normal"  # normal | secondary (2nd language) | caption | warning | small


@dataclass
class Bullets:
    items: list[str]


@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    widths: list[float] | None = None  # relative column widths
    kind: str = "grid"  # grid | kv (key/value, no header row)
    row_tags: list[str] | None = None  # per-row tag used for colouring, e.g. action level


@dataclass
class Figure:
    png: bytes
    caption: list[str]
    width: float = 1.0  # fraction of text width
    aspect: float = 3.2 / 7.0  # height / width, for keeping the figure and its caption on one page


@dataclass
class PageBreak:
    pass


@dataclass
class Report:
    title: str
    subtitle: str
    lang: str
    draft: bool
    draft_text: list[str]
    draft_mark: str
    page_label: str
    footer: str
    blocks: list = field(default_factory=list)

    def all_text(self) -> str:
        """Every visible string, for wording checks and tests."""
        parts = [self.title, self.subtitle, self.footer, *self.draft_text]
        for b in self.blocks:
            if isinstance(b, (Heading, Para)):
                parts.append(b.text)
            elif isinstance(b, Bullets):
                parts.extend(b.items)
            elif isinstance(b, Table):
                parts.extend(b.header)
                parts.extend(c for r in b.rows for c in r)
            elif isinstance(b, Figure):
                parts.extend(b.caption)
        return "\n".join(parts)


@dataclass
class Signoff:
    """What the review app adds to the zone records: who confirmed what, from which files.

    draft_reasons are (code, params) pairs translated as draft_<code>; any reason makes the report a
    watermarked draft. Line dicts carry line_id, status, status_note, sections and n_manual; inputs carry
    name, size and sha256 of each data file; snapshots carry png, caption, source and created.
    """
    draft_reasons: list[tuple[str, dict]] = field(default_factory=list)
    reviewer: str = ""
    generated_at: str = ""
    app_version: str = ""
    lines: list[dict] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    map_png: bytes | None = None
    snapshots: list[dict] = field(default_factory=list)


def png_aspect(png: bytes) -> float:
    """height / width from the PNG header."""
    import struct

    w, h = struct.unpack(">II", png[16:24])
    return h / w if w else 1.0


def fmt(x: float | None, nd: int = 2) -> str:
    return "–" if x is None else f"{x:.{nd}f}"


def _ids(zones: list[Zone]) -> str:
    return ", ".join(z.zone_id for z in zones)


class _Composer:
    def __init__(self, survey: SurveyResult, lang: str, figures: bool, signoff: Signoff | None = None) -> None:
        self.s = survey
        self.so = signoff
        self.tr = Translator(lang)
        self.t = self.tr.t
        self.figures = figures
        self.blocks: list = []
        self.fig_no = 0
        self.surface = survey.info.surface_use
        self.actions = {id(z): suggest_action(z, self.surface) for z in survey.zones}

    # helpers -------------------------------------------------------------
    def paras(self, key: str, style: str = "normal", **p) -> None:
        texts = self.tr.paras(key, **p)
        for i, txt in enumerate(texts):
            self.blocks.append(Para(txt, style if i == 0 else ("secondary" if style == "normal" else style)))

    def action(self, z: Zone) -> str:
        return self.actions[id(z)]

    def real_zones(self) -> list[Zone]:
        return [z for z in self.s.zones if not z.is_artifact and z.review != "rejected"]

    def vsrc(self, src: str) -> str:
        key = f"vsrc_{src}"
        return self.t(key) if self.tr.has(key) else src

    def blank(self, v) -> str:
        return self.t("not_recorded") if v in ("", None) else str(v)

    # sections ------------------------------------------------------------
    def summary(self) -> None:
        self.blocks.append(Heading(self.t("sec_summary")))
        zones = self.real_zones()
        n_art = sum(1 for z in self.s.zones if z.is_artifact) + sum(len(ln.artifact_regions) for ln in self.s.lines)
        self.paras("sum_counts", lines=len(self.s.lines), zones=len(zones), artifacts=n_art)
        self.paras("sum_two_axes", style="small")

        counts = {a: 0 for a in ACTION_LEVELS}
        for z in zones:
            counts[self.action(z)] += 1
        self.blocks.append(Heading(self.t("sum_action_counts"), 3))
        self.blocks.append(Table(
            header=[self.t("action"), self.t("zone"), ""],
            rows=[[self.t(f"act_{a}"), str(counts[a]), _ids([z for z in zones if self.action(z) == a]) or "–"]
                  for a in ACTION_LEVELS],
            widths=[2.2, 0.8, 4], row_tags=list(ACTION_LEVELS)))
        self.paras("action_provisional", style="small", surface=self.t(f"surface_{self.surface}"))

        follow = sorted((z for z in zones if self.action(z) in ("E", "1", "2")),
                        key=lambda z: (action_rank(self.action(z)), z.zone_id))
        self.blocks.append(Heading(self.t("sum_followup"), 3))
        if follow:
            self.blocks.append(Bullets([self.zone_oneliner(z) for z in follow]))
        else:
            self.paras("sum_none_followup")

    def zone_oneliner(self, z: Zone) -> str:
        return (f"{z.zone_id} ({self.t('line')} {z.line_id}, trace {z.trace_start}–{z.trace_end}, "
                f"{fmt(z.depth_top_m, 1)}–{fmt(z.depth_bottom_m, 1)} m): {self.t('type_' + z.suspected_type)} · "
                f"{self.t('confidence')} {self.t('conf_' + z.confidence)} · {self.t('act_' + self.action(z))}")

    def job(self) -> None:
        i = self.s.info
        self.blocks.append(Heading(self.t("sec_job")))
        rows = [
            [self.t("project"), self.blank(i.project)],
            [self.t("site"), self.blank(i.site)],
            [self.t("client"), self.blank(i.client)],
            [self.t("survey_date"), self.blank(i.survey_date)],
            [self.t("operator"), self.blank(i.operator)],
            [self.t("instrument"), self.blank(i.instrument)],
            [self.t("antenna"), self.blank(i.antenna)],
            [self.t("frequency"), self.blank(None if i.frequency_mhz is None else f"{i.frequency_mhz:g}")],
            [self.t("surface_use"), self.t(f"surface_{i.surface_use}")],
            [self.t("datum"), self.blank(i.datum)],
            [self.t("n_lines"), str(len(self.s.lines))],
        ]
        self.blocks.append(Table(header=[], rows=rows, widths=[1.3, 3], kind="kv"))
        self.blocks.append(Table(
            header=[self.t("line"), self.t("source_file"), self.t("n_traces"), self.t("time_window"),
                    self.t("trace_spacing"), self.t("line_length")],
            rows=[[ln.line_id, ln.source_file, str(ln.n_traces), fmt(ln.time_window_ns, 1),
                   fmt(ln.trace_spacing_m, 3),
                   "–" if ln.trace_spacing_m is None else fmt((ln.n_traces - 1) * ln.trace_spacing_m, 1)]
                  for ln in self.s.lines],
            widths=[1, 3, 1, 1.2, 1.2, 1.2]))

    def processing(self) -> None:
        self.blocks.append(Heading(self.t("sec_processing")))
        key = lambda ln: [(p.name, sorted(p.params.items()), p.auto) for p in ln.processing]  # noqa: E731
        groups: list[tuple[list[LineResult], LineResult]] = []
        for ln in self.s.lines:
            for g in groups:
                if key(g[1]) == key(ln):
                    g[0].append(ln)
                    break
            else:
                groups.append(([ln], ln))
        for members, ln in groups:
            label = self.t("proc_same_all") if len(groups) == 1 and len(self.s.lines) > 1 else \
                f"{self.t('line')} {', '.join(m.line_id for m in members)}"
            self.blocks.append(Heading(label, 3))
            if not ln.processing:
                self.paras("proc_none")
                continue
            self.blocks.append(Table(
                header=[self.t("step"), self.t("parameters"), self.t("chosen_by")],
                rows=[[self.t(f"step_{p.name}") if self.tr.has(f"step_{p.name}") else p.name,
                       ", ".join(f"{k} = {v}" for k, v in p.params.items()) or "–",
                       self.t("auto") if p.auto else self.t("user")] for p in ln.processing],
                widths=[2, 4, 1]))
        self.blocks.append(Table(
            header=[self.t("line"), self.t("velocity"), self.t("velocity_source")],
            rows=[[ln.line_id, fmt(ln.velocity_m_per_ns, 3), self.vsrc(ln.velocity_source)] for ln in self.s.lines],
            widths=[1, 1.5, 3]))
        self.paras("two_tracks", style="small")

    def lines(self) -> None:
        from gpr_analyzer.report import figures as figs

        self.blocks.append(Heading(self.t("sec_lines")))
        if self.figures and self.so and self.so.map_png:
            self.fig_no += 1
            self.blocks.append(Figure(self.so.map_png, self.tr.paras("fig_map", n=self.fig_no),
                                      width=0.8, aspect=png_aspect(self.so.map_png)))
        for ln in self.s.lines:
            self.blocks.append(Heading(f"{self.t('line')} {ln.line_id}", 2))
            zs = [z for z in ln.zones if z.review != "rejected"]
            if self.figures and ln.radargram is not None:
                for view, key in (("gray", "fig_gray"), ("envelope", "fig_color")):
                    self.fig_no += 1
                    png = figs.render_line(ln, view=view, actions={z.zone_id: self.action(z) for z in zs})
                    self.blocks.append(Figure(png, self.tr.paras(key, n=self.fig_no, line=ln.line_id)))
            elif self.figures:
                self.paras("no_figure", style="small")
            if zs:
                self.blocks.append(Bullets([self.zone_oneliner(z) for z in zs]))
            else:
                self.paras("no_zones")
            if ln.artifact_regions:
                self.blocks.append(Para(self.t("artifacts_on_line") + ": " + "; ".join(
                    f"trace {a.trace_start}–{a.trace_end} ({self.t('art_' + a.reason) if self.tr.has('art_' + a.reason) else a.reason})"
                    for a in ln.artifact_regions), "small"))

    def snapshots(self) -> None:
        if not (self.figures and self.so and self.so.snapshots):
            return
        self.blocks.append(Heading(self.t("sec_snapshots"), 2))
        self.paras("snap_note", style="small")
        for snap in self.so.snapshots:
            self.fig_no += 1
            key = "snap_src_" + snap.get("source", "other")
            key = key if self.tr.has(key) else "snap_src_other"
            langs = ("th", "en") if self.tr.lang == "bi" else (self.tr.lang,)
            cap = [f"{self.t('figure')} {self.fig_no}: {snap.get('caption') or self.t(key)}"]
            cap += [Translator(lg).t("snap_meta", src=Translator(lg).t(key), when=snap.get("created", "")) for lg in langs]
            if snap.get("source") == "3d":
                cap += self.tr.paras("snap_3d_note")
            self.blocks.append(Figure(snap["png"], cap, aspect=png_aspect(snap["png"])))

    def zone_table(self) -> None:
        self.blocks.append(PageBreak())
        self.blocks.append(Heading(self.t("sec_zones")))
        zones = sorted((z for z in self.s.zones if z.review != "rejected"),
                       key=lambda z: (action_rank(self.action(z)), z.line_id, z.zone_id))
        if not zones:
            self.paras("no_zones")
            return
        self.blocks.append(Table(
            header=[self.t("zone"), self.t("line"), self.t("traces"), self.t("distance_m"), self.t("depth_m"),
                    self.t("length_m"), self.t("type"), self.t("confidence"), self.t("action"), self.t("flags")],
            rows=[[z.zone_id, z.line_id, f"{z.trace_start}–{z.trace_end}",
                   "–" if z.distance_start_m is None else f"{fmt(z.distance_start_m, 1)}–{fmt(z.distance_end_m, 1)}",
                   f"{fmt(z.depth_top_m, 1)}–{fmt(z.depth_bottom_m, 1)}", fmt(z.length_m, 1),
                   self.t("type_" + z.suspected_type), self.t("conf_" + z.confidence),
                   self.t("act_" + self.action(z)), "; ".join(z.artifact_flags) or "–"] for z in zones],
            widths=[0.7, 0.6, 1.1, 1.1, 1, 0.7, 1.6, 1, 1.3, 1.6],
            row_tags=[self.action(z) for z in zones]))
        self.paras("sum_two_axes", style="small")

        self.blocks.append(Heading(self.t("sec_cards"), 2))
        for z in zones:
            self.zone_card(z)

    def zone_card(self, z: Zone) -> None:
        self.blocks.append(Heading(f"{self.t('zone')} {z.zone_id} – {self.t('type_' + z.suspected_type)}", 3))
        loc = f"{self.t('line')} {z.line_id} · trace {z.trace_start}–{z.trace_end}"
        if z.distance_start_m is not None:
            loc += f" · {fmt(z.distance_start_m, 1)}–{fmt(z.distance_end_m, 1)} m"
        coords = "–"
        if z.coord_start is not None:
            coords = f"{z.coord_start[0]:.6f}, {z.coord_start[1]:.6f}"
            if z.coord_end is not None:
                coords += f" → {z.coord_end[0]:.6f}, {z.coord_end[1]:.6f}"
            coords += f" ({z.datum or self.s.info.datum or self.t('not_recorded')})"
        depth = self.t("depth_with_v", top=fmt(z.depth_top_m), bottom=fmt(z.depth_bottom_m),
                       v=fmt(z.velocity_m_per_ns, 3), src=self.vsrc(z.velocity_source))
        if z.velocity_uncertainty_m_per_ns:
            lo, hi = z.depth_range_with_uncertainty()
            depth += " · " + self.t("depth_uncertain", dv=fmt(z.velocity_uncertainty_m_per_ns, 3),
                                    top=fmt(lo), bottom=fmt(hi))
        gt = self.t("no_ground_truth") if not z.ground_truth else \
            ", ".join(f"{k}: {v}" for k, v in z.ground_truth.items())
        rows = [
            [self.t("location"), loc],
            [self.t("coords"), coords],
            [self.t("depth_m"), depth],
            [self.t("time_ns"), f"{fmt(z.time_top_ns, 1)}–{fmt(z.time_bottom_ns, 1)}"],
            [self.t("confidence"), self.t("conf_" + z.confidence)],
            [self.t("action"), self.t("act_" + self.action(z))],
            [self.t("review_state"), self.t("review_" + z.review)],
            [self.t("ground_truth"), gt],
        ]
        if z.note:
            rows.append([self.t("note"), z.note])
        self.blocks.append(Table(header=[], rows=rows, widths=[1.3, 4], kind="kv"))
        if z.evidence:
            items = []
            for e in z.evidence:
                items.append(f"{EVIDENCE_MARK[e.kind]} {self.evidence_text(e)}")
            self.blocks.append(Para(self.t("evidence"), "small"))
            self.blocks.append(Bullets(items))

    def evidence_text(self, e) -> str:
        th, en = e.text, e.params.get("text_en", "")
        if th or en:
            th, en = th or en, en or th
            return {"th": th, "en": en}.get(self.tr.lang) or (th if th == en else f"{th} / {en}")
        key = f"ev_{e.code}"
        return self.t(key, **e.params) if self.tr.has(key) else e.code

    def limitations(self) -> None:
        self.blocks.append(Heading(self.t("sec_limits")))
        items: list[str] = []
        for ln in self.s.lines:
            zs = [z for z in ln.zones if not z.is_artifact and z.review != "rejected"] or None
            dv = max((z.velocity_uncertainty_m_per_ns or 0 for z in zs), default=0) if zs else 0
            dv = dv or 0.02
            d = max((z.depth_bottom_m for z in zs), default=None) if zs else None
            if d is None:
                d = float(depth_from_time(ln.time_window_ns, ln.velocity_m_per_ns, ln.antenna_separation_m))
            items += [f"{self.t('line')} {ln.line_id}: {p}" for p in self.tr.paras(
                "lim_velocity", v=fmt(ln.velocity_m_per_ns, 3), src=self.vsrc(ln.velocity_source),
                dv=fmt(dv, 3), d=fmt(d, 1), dd=fmt(d * dv / ln.velocity_m_per_ns, 1))]
        default_v = [ln.line_id for ln in self.s.lines if ln.velocity_source == "default"]
        if default_v:
            items += self.tr.paras("lim_default_velocity", lines=", ".join(default_v))
        for key in ("lim_single_line", "lim_grades", "lim_colour", "lim_artifacts"):
            items += self.tr.paras(key)
        self.blocks.append(Bullets(items))

    def recommendations(self) -> None:
        self.blocks.append(Heading(self.t("sec_recs")))
        zones = self.real_zones()
        by = {a: [z for z in zones if self.action(z) == a] for a in ACTION_LEVELS}
        items: list[str] = []
        if by["E"]:
            items += self.tr.paras("rec_emergency", zones=_ids(by["E"]))
        if by["E"] or by["1"]:
            items += self.tr.paras("rec_verify", zones=_ids(by["E"] + by["1"]))
        investigate = by["1"] + by["2"]
        if investigate:
            items += self.tr.paras("rec_grid", zones=_ids(investigate))
            items += self.tr.paras("rec_geophys", zones=_ids(investigate))
        if by["3"]:
            items += self.tr.paras("rec_monitor", zones=_ids(by["3"]))
        weak_v = [z for z in by["E"] + by["1"] + by["2"] if z.velocity_source == "default"]
        if weak_v:
            items += self.tr.paras("rec_velocity", zones=_ids(weak_v))
        unrev = [z for z in zones if z.review == "auto"]
        if unrev:
            items += self.tr.paras("rec_review", zones=_ids(unrev))
        for r in self.s.recommendations:
            text = r.get(self.tr.lang) or r.get("text") or r.get("en") or r.get("th", "")
            if self.tr.lang == "bi" and r.get("th") and r.get("en"):
                text = f"{r['th']} / {r['en']}"
            ids = r.get("zone_ids") or []
            items.append(f"{text} ({', '.join(ids)})" if ids else text)
        self.blocks.append(Bullets(items or self.tr.paras("rec_none")))

    def review_record(self) -> None:
        """Who confirmed the results, what was rejected or left out, and the exact input files."""
        so = self.so
        self.blocks.append(Heading(self.t("sec_record")))
        status = self.t("status_draft") if self.draft else self.t("status_final")
        rows = [[self.t("report_status"), status]]
        if so is not None:
            rows += [[self.t("reviewer"), self.blank(so.reviewer)],
                     [self.t("generated_at"), self.blank(so.generated_at)],
                     [self.t("software"), f"gpr-analyzer {so.app_version}".strip()]]
        self.blocks.append(Table(header=[], rows=rows, widths=[1.3, 4], kind="kv"))
        if self.draft and so is not None and so.draft_reasons:
            self.blocks.append(Bullets([self.t("draft_" + c, **p) for c, p in so.draft_reasons]))

        meta = {d["line_id"]: d for d in (so.lines if so else [])}
        rows = []
        for ln in self.s.lines:
            m = meta.get(ln.line_id, {})
            n = {r: sum(1 for z in ln.zones if z.review == r) for r in ("accepted", "edited", "rejected", "auto")}
            st = self.t("lstatus_" + m["status"]) if m.get("status") else "–"
            if m.get("status_note"):
                st += f" ({m['status_note']})"
            secs = "; ".join(f"{x['section_id']} trace {x['trace_start']}–{x['trace_end']}"
                             + (f" ({x['reason']})" if x.get("reason") else "") for x in m.get("sections", [])) or "–"
            rows.append([ln.line_id, st, str(n["accepted"]), str(n["edited"]), str(m.get("n_manual", "–")),
                         str(n["rejected"]), str(n["auto"]), secs])
        self.blocks.append(Table(
            header=[self.t("line"), self.t("line_status"), self.t("col_accepted"), self.t("col_edited"),
                    self.t("n_manual"), self.t("col_rejected"), self.t("col_auto"), self.t("excluded_sections")],
            rows=rows, widths=[0.8, 1.6, 0.9, 0.9, 0.9, 0.9, 0.9, 2.4]))
        self.paras("record_counts_note", style="small")

        rejected = [z for z in self.s.zones if z.review == "rejected"]
        self.blocks.append(Heading(self.t("rejected_title"), 3))
        if rejected:
            self.blocks.append(Table(
                header=[self.t("zone"), self.t("line"), self.t("traces"), self.t("depth_m"), self.t("type"),
                        self.t("note")],
                rows=[[z.zone_id, z.line_id, f"{z.trace_start}–{z.trace_end}",
                       f"{fmt(z.depth_top_m, 1)}–{fmt(z.depth_bottom_m, 1)}", self.t("type_" + z.suspected_type),
                       z.note or "–"] for z in sorted(rejected, key=lambda z: (z.line_id, z.zone_id))],
                widths=[0.7, 0.7, 1.2, 1, 1.6, 3]))
        else:
            self.paras("rejected_none")

        if so is not None and so.inputs:
            self.blocks.append(Heading(self.t("inputs_title"), 3))
            self.blocks.append(Table(
                header=[self.t("source_file"), self.t("file_size"), "SHA-256"],
                rows=[[f["name"], f"{f['size']:,}", f["sha256"]] for f in so.inputs], widths=[2.2, 1, 4.4]))
            self.paras("inputs_note", style="small")

    def glossary(self) -> None:
        self.blocks.append(Heading(self.t("sec_glossary"), 2))
        self.blocks.append(Table(header=[self.t("confidence"), ""],
                                 rows=[[self.t(f"conf_{c}"), " / ".join(self.tr.paras(f"conf_def_{c}"))]
                                       for c in ("confirmed", "high", "medium", "low")], widths=[1.3, 4]))
        self.blocks.append(Table(header=[self.t("action"), ""],
                                 rows=[[self.t(f"act_{a}"), " / ".join(self.tr.paras(f"act_def_{a}"))]
                                       for a in ACTION_LEVELS], widths=[1.3, 4], row_tags=list(ACTION_LEVELS)))

    def build(self) -> Report:
        i = self.s.info
        self.draft = self.s.unreviewed or bool(self.so and self.so.draft_reasons)
        sections = [self.summary, self.job, self.processing, self.lines, self.snapshots, self.zone_table,
                    self.limitations, self.recommendations]
        if self.so is not None:
            sections.append(self.review_record)
        for section in sections + [self.glossary]:
            section()
        draft_text = self.tr.paras("draft_banner")
        if self.so is not None and self.so.draft_reasons:
            draft_text = self.tr.paras("draft_banner_reasons") + [
                "• " + self.t("draft_" + c, **p) for c, p in self.so.draft_reasons]
        sub = " · ".join(x for x in (i.project, i.site) if x)
        meta = [f"{self.t('report_date')}: {i.report_date or date.today().isoformat()}"]
        if i.report_id:
            meta.insert(0, f"{self.t('report_id')}: {i.report_id}")
        return Report(
            title=self.t("report_title"), subtitle=" · ".join([sub, *meta]) if sub else " · ".join(meta),
            lang=self.tr.lang, draft=self.draft, draft_text=draft_text,
            draft_mark=self.t("draft_mark"), page_label=self.t("page"),
            footer=f"{self.t('generated_by')} · {i.report_id or ''}".rstrip(" ·"), blocks=self.blocks)


def compose(survey: SurveyResult, lang: str = "bi", figures: bool = True, signoff: Signoff | None = None) -> Report:
    """Build the report content. figures=False skips matplotlib (fast tests).

    signoff (from the review app) adds the review record, map and snapshots, and makes the report a draft
    while any of its draft_reasons remain.
    """
    return _Composer(survey, lang, figures, signoff).build()
