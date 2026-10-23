import numpy as np
import pytest

from gpr_analyzer import Radargram, cli
from gpr_analyzer.report.schema import Zone


def test_demo_writes_every_requested_report(tmp_path, capsys):
    pdf, docx = tmp_path / "a.pdf", tmp_path / "a.docx"
    assert cli.main(["analyze", "--demo", "--report", str(pdf), "-o", str(docx), "--lang", "en"]) == 0
    assert pdf.read_bytes().startswith(b"%PDF") and docx.stat().st_size > 0


def test_no_reader_yet_gives_clear_message(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "find_hook", lambda stage: None)
    with pytest.raises(SystemExit, match="--demo"):
        cli.main(["analyze", str(tmp_path / "x.iprb"), "--report", str(tmp_path / "r.pdf")])


def test_pipeline_wiring_with_stub_stages(tmp_path, monkeypatch):
    r = Radargram(np.random.default_rng(0).normal(size=(200, 80)), dt_ns=0.5, dx_m=0.05)

    def process(rg):
        return rg.copy_with(data=rg.data * 2), [("dewow", {"window_ns": 5}), {"name": "gain_sec", "params": {}}]

    def detect(rg, line_id):
        return [Zone("Z1", line_id, 10, 30, 20.0, 40.0, rg.velocity_m_per_ns, "user",
                     "void_like", "low")]

    hooks = {"read": lambda path: [r], "process": process, "detect": detect}
    monkeypatch.setattr(cli, "find_hook", lambda stage: hooks[stage])
    s = cli.build_survey(["one.iprb", "two.iprb"], cli.SurveyInfo(surface_use="road"), velocity=0.12)
    assert [ln.line_id for ln in s.lines] == ["L1", "L2"]
    ln = s.lines[0]
    assert ln.velocity_m_per_ns == 0.12 and ln.velocity_source == "user"
    assert [p.name for p in ln.processing] == ["dewow", "gain_sec"]
    assert ln.zones[0].line_id == "L1" and ln.source_file == "one.iprb"
    out = cli.write_report(s, tmp_path / "r.pdf", lang="th")
    assert out.read_bytes().startswith(b"%PDF")
