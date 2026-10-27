"""Measure the automatic detection on real lines: what it proposes, and how that compares with what people
marked or decided.

Two honest yardsticks exist before anything is dug or drilled:

* reviewer decisions in the app's labels.jsonl: of the zones the program proposed, which did the
  reviewer accept (or accept after editing) and which did they reject, and what did they draw that the
  program missed. This is precision as judged by a person, per type and level.
* ViewPoint field markers (.mrk) put by the operator on site: how many have a suspicious zone near them.
  Markers are not ground truth (some mark a start, a kerb, a manhole), so this is only a rough recall.

Neither is a calibrated probability. Results go to CSV and a short markdown summary
(scripts/eval_detect_real.py).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SUSPICIOUS = ("void_like", "scattered", "pipe_point", "unknown")
LEVELS = ("high", "medium", "low")


# --------------------------------------------------------------------------- reviewer labels

@dataclass
class LabelTally:
    accepted: Counter = field(default_factory=Counter)  # (type, level) → n
    rejected: Counter = field(default_factory=Counter)
    edited: Counter = field(default_factory=Counter)  # accepted after a change (counted in accepted too)
    type_changed: Counter = field(default_factory=Counter)  # (auto type, reviewer type) → n
    added: Counter = field(default_factory=Counter)  # type drawn by the reviewer: a miss of the program
    n_records: int = 0

    def precision(self, key: tuple[str, str] | None = None) -> tuple[int, int, float | None]:
        """(accepted, decided, accepted / decided) for one (type, level), or over everything."""
        if key is None:
            a, r = sum(self.accepted.values()), sum(self.rejected.values())
        else:
            a, r = self.accepted[key], self.rejected[key]
        return a, a + r, (a / (a + r) if a + r else None)


def tally_labels(paths: Iterable[Path]) -> LabelTally:
    """The last decision per (job, line, zone) counts; 'reset' takes a decision back, deleting a
    proposed zone counts as rejecting it."""
    t = LabelTally()
    last: dict[tuple, dict[str, Any]] = {}
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            t.n_records += 1
            key = (rec.get("job_id"), rec.get("line_id"), rec.get("zone_id"))
            act = rec.get("action")
            if act in ("accept", "reject", "edit"):
                last[key] = rec
            elif act == "delete" and rec.get("auto"):  # deleting a proposed zone rejects it
                last[key] = {**rec, "action": "reject"}
            elif act == "reset":
                last.pop(key, None)
            elif act in ("add", "add_zone") and rec.get("after"):
                t.added[rec["after"].get("suspected_type", "unknown")] += 1
    for rec in last.values():
        auto = rec.get("auto")
        if not auto:  # a zone the reviewer drew: its fate says nothing about the program
            continue
        k = (auto.get("suspected_type"), auto.get("confidence"))
        if rec["action"] == "reject":
            t.rejected[k] += 1
            continue
        t.accepted[k] += 1
        if rec["action"] == "edit":
            t.edited[k] += 1
            after = rec.get("after") or {}
            if after.get("suspected_type") and after["suspected_type"] != auto.get("suspected_type"):
                t.type_changed[(auto.get("suspected_type"), after["suspected_type"])] += 1
    return t


def labels_table(t: LabelTally) -> list[dict[str, Any]]:
    rows = []
    for typ in sorted({k[0] for k in list(t.accepted) + list(t.rejected)}):
        for lvl in LEVELS:
            a, n, prec = t.precision((typ, lvl))
            if n:
                rows.append({"type": typ, "level": lvl, "accepted": a, "decided": n,
                             "precision": None if prec is None else round(prec, 3), "edited": t.edited[(typ, lvl)]})
    return rows


# --------------------------------------------------------------------------- field markers

def marker_hits(markers: list[dict[str, Any]], zones: list[dict[str, Any]], dt_ns: float, zero_sample: int,
                dx_m: float | None, along_m: float = 0.5, time_ns: float = 3.0) -> list[dict[str, Any]]:
    """For each marker: the nearest suspicious zone that holds it within along_m and time_ns, if any.

    Marker samples are in the raw record; zones are measured from time zero (the header ZERO LEVEL).
    """
    tol_tr = int(round(along_m / dx_m)) if dx_m else 10
    out = []
    for m in markers:
        t = (m["sample"] - zero_sample) * dt_ns
        hit = None
        for z in zones:
            if z["suspected_type"] not in SUSPICIOUS:
                continue
            if (z["trace_start"] - tol_tr <= m["trace"] <= z["trace_end"] + tol_tr
                    and z["time_top_ns"] - time_ns <= t <= z["time_bottom_ns"] + time_ns):
                hit = z
                break
        out.append({"trace": m["trace"], "sample": m["sample"], "time_ns": round(t, 2),
                    "zone_id": hit["zone_id"] if hit else None, "zone_type": hit["suspected_type"] if hit else None,
                    "zone_level": hit["confidence"] if hit else None})
    return out


# --------------------------------------------------------------------------- per line counts

def line_counts(zones: list[dict[str, Any]], artifacts: list[dict[str, Any]], length_m: float | None) -> dict[str, Any]:
    c = Counter((z["suspected_type"], z["confidence"]) for z in zones)
    row: dict[str, Any] = {f"{t}_{lvl}": c[(t, lvl)] for t in SUSPICIOUS for lvl in LEVELS}
    row["layer"] = sum(v for (t, _), v in c.items() if t == "layer")
    row["artifact_zones"] = sum(v for (t, _), v in c.items() if t == "artifact")
    a = Counter(x["reason"] for x in artifacts)
    for k in ("vertical_stripe", "dead_trace", "clipped_trace", "stationary_traces", "ringing", "bottom_noise"):
        row[f"flag_{k}"] = a[k]
    med_plus = sum(c[(t, lvl)] for t in SUSPICIOUS for lvl in ("high", "medium"))
    row["suspicious_medium_plus"] = med_plus
    row["per_10m_medium_plus"] = round(10 * med_plus / length_m, 2) if length_m else None
    return row


def summary_markdown(lines: list[dict[str, Any]], markers: list[dict[str, Any]], labels: LabelTally | None,
                     cross: Counter | None) -> str:
    n = len(lines)
    length = sum(r.get("length_m") or 0 for r in lines)
    med = sum(r["suspicious_medium_plus"] for r in lines)
    out = ["# Automatic detection on real lines", "",
           f"Lines: {n}, total {length:.0f} m. Suspicious zones at medium or high: {med} "
           f"({10 * med / length:.2f} per 10 m)." if length else f"Lines: {n}.", ""]
    by = Counter()
    for r in lines:
        for t in SUSPICIOUS:
            for lvl in LEVELS:
                by[(t, lvl)] += r[f"{t}_{lvl}"]
    out += ["| type | high | medium | low |", "|---|---|---|---|"]
    out += [f"| {t} | {by[(t, 'high')]} | {by[(t, 'medium')]} | {by[(t, 'low')]} |" for t in SUSPICIOUS]
    flags = Counter()
    for r in lines:
        for k, v in r.items():
            if k.startswith("flag_"):
                flags[k[5:]] += v
    out += ["", "Artifact flags: " + ", ".join(f"{k} {v}" for k, v in flags.items()), ""]
    if markers:
        hit = sum(m["zone_id"] is not None for m in markers)
        out += [f"ViewPoint markers with a suspicious zone nearby: {hit} of {len(markers)}. Markers are the "
                "operator's field notes, not ground truth.", ""]
    if cross:
        out += ["Cross-line check (GPS): " + ", ".join(f"{k} {v}" for k, v in sorted(cross.items())), ""]
    if labels and (labels.accepted or labels.rejected):
        a, d, p = labels.precision()
        out += [f"Reviewer decisions: {a} of {d} proposed zones accepted ({'' if p is None else f'{p:.2f}'} as a "
                "share, not a probability).", "", "| type | level | accepted | decided |", "|---|---|---|---|"]
        out += [f"| {r['type']} | {r['level']} | {r['accepted']} | {r['decided']} |" for r in labels_table(labels)]
        if labels.added:
            out += ["", "Drawn by the reviewer (missed by the program): "
                    + ", ".join(f"{k} {v}" for k, v in labels.added.items())]
    elif labels is not None:
        out += ["No reviewer decisions found in the labels given."]
    return "\n".join(out) + "\n"
