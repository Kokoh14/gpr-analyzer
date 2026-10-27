import json
import sys
from pathlib import Path

from gpr_analyzer.app.demo import write_demo_files
from gpr_analyzer.detect.evaluate import labels_table, marker_hits, tally_labels

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_detect_real  # noqa: E402


def _rec(action, zone_id, auto=None, after=None, job="j1", line="L1"):
    return json.dumps({"job_id": job, "line_id": line, "zone_id": zone_id, "action": action,
                       "auto": auto, "after": after})


def test_tally_uses_last_decision_and_counts_misses(tmp_path):
    void_m = {"suspected_type": "void_like", "confidence": "medium"}
    pipe_h = {"suspected_type": "pipe_point", "confidence": "high"}
    p = tmp_path / "labels.jsonl"
    p.write_text("\n".join([
        _rec("reject", "A", void_m), _rec("accept", "A", void_m),  # changed mind: accepted
        _rec("reject", "B", void_m),
        _rec("accept", "C", pipe_h), _rec("reset", "C", pipe_h),  # taken back: undecided
        _rec("edit", "D", pipe_h, after={"suspected_type": "void_like"}),
        _rec("delete", "E", pipe_h),
        _rec("add", "M1", None, after={"suspected_type": "void_like"}),
        _rec("accept", "M1", None),  # the reviewer's own zone says nothing about the program
    ]) + "\n", encoding="utf-8")
    t = tally_labels([p])
    assert t.precision(("void_like", "medium"))[:2] == (1, 2)
    assert t.precision(("pipe_point", "high"))[:2] == (1, 2)  # D accepted after edit, E deleted
    assert t.type_changed[("pipe_point", "void_like")] == 1 and t.added["void_like"] == 1
    assert t.precision()[:2] == (2, 4)
    assert {r["type"] for r in labels_table(t)} == {"void_like", "pipe_point"}


def test_marker_hits_use_time_zero():
    zones = [{"zone_id": "A", "suspected_type": "void_like", "confidence": "medium", "trace_start": 100,
              "trace_end": 150, "time_top_ns": 10.0, "time_bottom_ns": 14.0},
             {"zone_id": "L", "suspected_type": "layer", "confidence": "high", "trace_start": 0,
              "trace_end": 999, "time_top_ns": 30.0, "time_bottom_ns": 31.0}]
    marks = [{"trace": 160, "sample": 68 + 120}, {"trace": 400, "sample": 68 + 300}, {"trace": 120, "sample": 68 + 300}]
    hits = marker_hits(marks, zones, dt_ns=0.1, zero_sample=68, dx_m=0.0186)
    assert [h["zone_id"] for h in hits] == ["A", None, None]  # a layer does not count


def test_eval_script_runs_on_a_survey_folder(tmp_path):
    write_demo_files(tmp_path / "survey")
    labels = tmp_path / "labels.jsonl"
    labels.write_text(_rec("reject", "A", {"suspected_type": "pipe_point", "confidence": "high"}) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    assert eval_detect_real.main([str(tmp_path / "survey"), "--out", str(out), "--labels", str(labels)]) == 0
    for name in ("lines.csv", "zones.csv", "summary.md"):
        assert (out / name).stat().st_size > 0
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "Reviewer decisions: 0 of 1" in md and "%" not in md
    assert "cross_line" in (out / "zones.csv").read_text(encoding="utf-8-sig")
