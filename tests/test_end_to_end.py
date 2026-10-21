"""File → io.read → processing.auto_process → detect.detect_zones → report, on the synthetic
SubScan-like line written in every format the readers support.

The line (gpr_analyzer.synthetic.subscan_like_line) has known answers: v = 0.135 m/ns, a void-like
cavity under traces 150–300 with its roof at 5.25 m, a pipe at 3 m and a stripe band at traces 365–385.
These tests only prove the stages fit together and agree with the synthetic truth; no real file has
been through them yet.
"""

from __future__ import annotations

import numpy as np
import pytest

from gpr_analyzer.cli import analyze_radargram, build_survey, main
from gpr_analyzer.io import read
from gpr_analyzer.io.sniff import sniff, to_radargram
from gpr_analyzer.io.writers import write
from gpr_analyzer.report.schema import SurveyInfo
from gpr_analyzer.synthetic import subscan_like_line

FORMATS = ["LINE001.iprb", "LINE001.rd3", "FILE____001.DZT", "LINE00.DT1", "line001.sgy"]
CAVITY = (150, 300)
ROOF_M = 5.25
STRIPES = (365, 385)


@pytest.fixture(scope="module")
def scene():
    return subscan_like_line()


def _check_line(ln, truth, dx_m=0.05):
    assert ln.n_traces == 555
    assert ln.trace_spacing_m == pytest.approx(dx_m, rel=1e-3)
    assert ln.velocity_source == "hyperbola_fit"
    assert ln.velocity_m_per_ns == pytest.approx(truth.velocity_m_per_ns, rel=0.03)
    # one velocity for the whole line: the zone depths use the value printed in the line table
    assert all(z.velocity_m_per_ns == pytest.approx(ln.velocity_m_per_ns) for z in ln.zones)

    voids = [z for z in ln.zones if z.suspected_type == "void_like" and z.confidence in ("medium", "high")
             and z.trace_start <= 225 <= z.trace_end]
    assert len(voids) == 1, [(z.zone_id, z.trace_start, z.trace_end, z.suspected_type) for z in ln.zones]
    v = voids[0]
    assert v.action in ("1", "2", "3")  # a medium void-like zone always gets a field action
    assert 4.0 <= v.depth_top_m <= ROOF_M + 0.3  # the edge diffractions start a little above the roof

    pipes = [z for z in ln.zones if z.suspected_type == "pipe_point" and z.trace_start <= 60 <= z.trace_end]
    apex_ns = 2 * 1.2 / truth.velocity_m_per_ns
    assert pipes and pipes[0].time_top_ns <= apex_ns <= pipes[0].time_bottom_ns

    # the stripe band is flagged as an artifact and does not become a target
    a, b = STRIPES
    assert any(r.reason == "vertical_stripe" and r.trace_start <= a + 5 and r.trace_end >= b - 5
               for r in ln.artifact_regions)
    for z in ln.zones:
        if a <= z.trace_start and z.trace_end <= b:
            assert z.suspected_type not in ("void_like", "pipe_point"), z


@pytest.mark.parametrize("name", FORMATS)
def test_format_round_trip_and_pipeline(tmp_path, scene, name):
    rg, truth = scene
    path = write(rg, tmp_path / name)
    (back,) = read(path)
    assert back.data.shape == rg.data.shape
    assert back.dt_ns == pytest.approx(rg.dt_ns, rel=2e-3)
    assert back.dx_m == pytest.approx(rg.dx_m, rel=1e-3)
    # int16 formats are rescaled; the waveform must survive
    corr = np.corrcoef(back.data.ravel(), rg.data.ravel())[0, 1]
    assert corr > 0.999

    survey = build_survey([str(path)], SurveyInfo(project="e2e"))
    (ln,) = survey.lines
    assert ln.source_file == path.name
    assert [s.name for s in ln.processing][:3] == ["center_frequency", "dewow", "time_zero"]
    _check_line(ln, truth)


def test_unknown_format_via_sniffer(tmp_path, scene):
    """The SubScan path: an undocumented binary is sniffed with the trace count from the screen,
    then goes through the same stages."""
    rg, truth = scene
    data = np.round(rg.data / np.abs(rg.data).max() * 20000).astype(">i2")
    p = tmp_path / "LINE001.sub"
    p.write_bytes(b"SUBSCAN-LIKE TEST FILE".ljust(512, b"\0") + data.T.tobytes())
    _, cands = sniff(p, n_traces=555, top=10)
    best = cands[0]
    # the stride and sample type are found; the leading quiet samples before time zero can be
    # mistaken for a per-trace header (known sniffer ambiguity), so a person picks the 0-byte one
    assert (best.header_bytes, best.dtype, best.stride) == (512, ">i2", 2 * rg.n_samples)
    best = next(c for c in cands if c.stride == best.stride and c.trace_header_bytes == 0)
    r = to_radargram(p, best, dt_ns=rg.dt_ns, dx_m=rg.dx_m)
    ln = analyze_radargram(r, "L1", p.name)
    _check_line(ln, truth)


def test_user_velocity_is_kept_through_processing_and_detect(tmp_path, scene):
    rg, _ = scene
    path = write(rg, tmp_path / "LINE001.iprb")
    (ln,) = build_survey([str(path)], SurveyInfo(), velocity=0.12).lines
    assert ln.velocity_source == "user" and ln.velocity_m_per_ns == pytest.approx(0.12)
    assert all(z.velocity_m_per_ns == pytest.approx(0.12) for z in ln.zones)
    vstep = next(s for s in ln.processing if s.name == "velocity")
    assert vstep.params["velocity_m_per_ns"] == pytest.approx(0.12)


def test_cli_writes_every_report_format(tmp_path, scene, capsys):
    rg, _ = scene
    path = write(rg, tmp_path / "LINE001.iprb")
    outs = [tmp_path / f"report.{ext}" for ext in ("pdf", "docx", "html", "json", "csv")]
    args = ["analyze", str(path), "--surface", "road", "--project", "e2e"]
    for o in outs:
        args += ["-o", str(o)]
    assert main(args) == 0
    for o in outs:
        assert o.exists() and o.stat().st_size > 0, o
    assert outs[0].read_bytes()[:5] == b"%PDF-"
    assert "void_like" in outs[4].read_text(encoding="utf-8")
