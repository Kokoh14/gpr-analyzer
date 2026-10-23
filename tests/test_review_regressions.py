"""Cross-path depth consistency, stale navigation, retained reviews and bounded/reusable work."""
import gc
import pickle
import weakref
from types import SimpleNamespace

import numpy as np
import pytest

from gpr_analyzer.app import compare, geo, scene3d
from gpr_analyzer.app.demo import write_demo_files
from gpr_analyzer.app.files import preview_text
from gpr_analyzer.app.jobs import Job, JobStore, _stamp
from gpr_analyzer.core import Radargram, crop_to_range, depth_from_time
from gpr_analyzer.detect import analyze_line, to_report_records
from gpr_analyzer.detect import _signal
from gpr_analyzer.io import read
from gpr_analyzer.processing.auto import ProcessingOptions, run_pipeline
from gpr_analyzer.report.schema import LineResult, Zone
from gpr_analyzer.synthetic import make_scene


@pytest.fixture(scope="module")
def raw():
    return make_scene()[0]


@pytest.fixture(scope="module")
def processed(raw):
    return run_pipeline(raw, tracks={"analysis"}).analysis


@pytest.fixture()
def saved_job(tmp_path):
    paths = write_demo_files(tmp_path / "input")
    primary = next(p for p in paths if p.name == "DEMO_LINE01.iprh")
    job = JobStore(tmp_path / "jobs").new()
    job.load([primary])
    return job, primary


@pytest.mark.parametrize("separation", [0.0, 0.4])
def test_depth_agrees_across_detection_records_map_and_comparison(processed, separation):
    rg = processed.copy_with(metadata={"antenna_separation_m": separation})
    det = analyze_line(rg, velocity_m_per_ns=0.1)
    records, _ = to_report_records(det, "L1")
    assert records
    for detected, record in zip(det.zones, records):
        z = Zone.from_dict(record)
        assert z.depth_top_m == pytest.approx(detected.depth_top_m)
        assert z.depth_bottom_m == pytest.approx(detected.depth_bottom_m)
        assert detected.thickness_m == pytest.approx(z.depth_bottom_m - z.depth_top_m)
        assert Zone.from_dict(z.to_dict()).depth_top_m == z.depth_top_m
    geom = compare.LineGeom(555, 0.05, 100.0, 0.1, antenna_separation_m=separation)
    for t in [-1, 0, 10, 80]:
        d = float(depth_from_time(t, 0.1, separation))
        assert geom.depth(t) == pytest.approx(d)
        assert geom.ns(d, "m", None) == pytest.approx(t)
    sl = geo.energy_slices(np.ones((20, 4)), 0.5, 0.1, separation_m=separation)
    assert sl["depth_edges_m"][-1] == pytest.approx(float(depth_from_time(10, 0.1, separation)), abs=0.0005)


def test_old_zone_default_and_uncertainty_roundtrip():
    fields = dict(zone_id="A", line_id="L1", trace_start=0, trace_end=1, time_top_ns=10,
                  time_bottom_ns=20, velocity_m_per_ns=0.1, velocity_source="user",
                  suspected_type="unknown", confidence="low", velocity_uncertainty_m_per_ns=0.01)
    old = Zone.from_dict(fields)
    assert old.depth_top_m == 0.5
    new = Zone.from_dict({**fields, "antenna_separation_m": 0.4})
    assert new.depth_range_with_uncertainty() == pytest.approx((float(depth_from_time(10, 0.09, 0.4)), float(depth_from_time(20, 0.11, 0.4))))
    line = LineResult("L1", "test", 2, 20, 1.0, 0.1, 0.1, "user", zones=[new], antenna_separation_m=0.4)
    restored = LineResult.from_dict(line.to_dict())
    assert restored.zones[0].depth_top_m == new.depth_top_m


def test_requested_tracks_preserve_analysis_and_skip_unused_work(raw, monkeypatch):
    full = run_pipeline(raw, ProcessingOptions(velocity_m_per_ns=0.12))
    from gpr_analyzer.processing import auto
    monkeypatch.setattr(auto.F, "energy", lambda *a, **k: pytest.fail("unrequested energy"))
    monkeypatch.setattr(auto, "viewpoint_display", lambda *a, **k: pytest.fail("unrequested viewpoint"))
    small = run_pipeline(raw, ProcessingOptions(velocity_m_per_ns=0.12), tracks={"analysis", "envelope"})
    np.testing.assert_array_equal(small.analysis.data, full.analysis.data)
    np.testing.assert_array_equal(small.envelope.data, full.envelope.data)
    assert small.layers is small.energy is small.display is small.viewpoint is None
    assert not any(s.step in ("agc", "viewpoint_display") for s in small.log)


def test_detector_features_are_reused_without_changing_results(processed, monkeypatch):
    d = processed.data.astype(float)
    baseline = analyze_line(processed)
    assert baseline.zones
    before = baseline.to_json()
    env = _signal.envelope(d)
    old_end = _signal.direct_wave_end(d, _signal.dominant_period_samples(d))
    old_sign = _signal.surface_polarity(d, _signal.dominant_period_samples(d))
    monkeypatch.setattr(_signal, "envelope", lambda *a: pytest.fail("duplicate Hilbert"))
    contrast, period, end = _signal.contrast_section(d, env)
    assert end == old_end
    assert _signal.surface_polarity(d, period, end) == old_sign
    assert analyze_line(processed, envelope_data=env).to_json() == before


@pytest.mark.parametrize("family,suffix,dtype", [("impulseradar", ".iprb", "<i4"), ("mala", ".rd3", "<i2"), ("mala", ".rd7", "<i4")])
def test_ranged_reader_reads_only_selected_traces(tmp_path, monkeypatch, family, suffix, dtype):
    stem = tmp_path / "GRID_0"
    primary = stem.with_suffix(".iprh" if family == "impulseradar" else ".rad")
    primary.write_text("SAMPLES: 32\nTIMEWINDOW: 16\nDISTANCE INTERVAL: 0.1\nDATA VERSION: 32\nANTENNA SEPARATION: 0.4\n")
    payload = np.arange(100 * 32, dtype=dtype).reshape(100, 32)
    payload[32] = 0
    payload.tofile(stem.with_suffix(suffix))
    (tmp_path / "GRID.cor").write_text("1 2026 12:00 13 N 100 E 0\n100 2026 12:01 13.001 N 100 E 0\n")
    full = read(stem.with_suffix(suffix))[0]
    expected, offset = crop_to_range(full, 2, 5)
    calls = []
    original = np.fromfile
    def counted(*a, **kw):
        calls.append(kw)
        return original(*a, **kw)
    monkeypatch.setattr(np, "fromfile", counted)
    cropped = read(stem.with_suffix(suffix), range_m=(2, 5))[0]
    np.testing.assert_array_equal(cropped.data, expected.data)
    assert cropped.metadata == expected.metadata
    assert offset == 20 and calls[0]["count"] == 31 * 32
    assert calls[0]["offset"] == 20 * 32 * np.dtype(dtype).itemsize


def test_shared_navigation_and_marker_changes_invalidate_stamp(tmp_path):
    p = tmp_path / "GRID_0.iprh"
    p.write_text("header")
    p.with_suffix(".iprb").write_bytes(b"data")
    first = _stamp(p)
    (tmp_path / "GRID.cor").write_text("GPS")
    second = _stamp(p)
    p.with_suffix(".mrk").write_text("marker")
    assert first != second != _stamp(p)
    assert {x["name"] for x in _stamp(p)} == {"GRID_0.iprh", "GRID_0.iprb", "GRID.cor", "GRID_0.mrk"}


def test_stale_checkpoint_resume_keeps_options_and_review_across_restarts(saved_job):
    job, primary = saved_job
    ln = job.reprocess("L1", {"velocity_m_per_ns": 0.12})
    assert ln.zones
    accepted = ln.zones[0].zone_id
    job.review("L1", accepted, "accept", note="retain acceptance")
    manual = job.add_zone("L1", {"trace_start": 30, "trace_end": 45, "time_top_ns": 10, "time_bottom_ns": 20, "note": "retain manual"})
    ln.zone(accepted).ground_truth = {"method": "synthetic fixture", "result": "test only"}
    ln.zone(accepted).confidence = "confirmed"
    job.save()
    labels = job.labels_path.read_bytes()
    checkpoint = job.checkpoint_path("L1")
    with checkpoint.open("rb") as stream:
        rec = pickle.load(stream)
    rec["processing_revision"] = -1
    with checkpoint.open("wb") as stream:
        pickle.dump(rec, stream)
    pending = Job.restore(job.folder)
    assert not pending.lines
    pending.save()  # opening/saving while awaiting resume must not erase pending review state
    reopened = Job.restore(job.folder)
    reopened.resume_now()
    ln2 = reopened.line("L1")
    assert ln2.result.velocity_m_per_ns == 0.12
    assert ln2.zone(accepted).review == "accepted" and ln2.zone(accepted).note == "retain acceptance"
    assert ln2.zone(accepted).confidence == "confirmed" and ln2.zone(accepted).ground_truth
    assert ln2.zone(manual.zone_id).note == "retain manual"
    assert reopened.labels_path.read_bytes() == labels
    assert reopened.read_checkpoint(reopened.plan[0], "L1") is not None


def test_texture_reuses_contrast_and_pixels_then_invalidates(tmp_path, monkeypatch):
    rg = Radargram(np.arange(400, dtype=np.float32).reshape(20, 20), 0.5)
    ln = SimpleNamespace(result=SimpleNamespace(analysis=rg), _cache={})
    rows, cols = np.arange(20), np.arange(20)
    original, calls = np.percentile, []
    def counted(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(np, "percentile", counted)
    before = rg.data.copy()
    png = scene3d._texture(ln, rows, cols, "grey")
    assert scene3d._texture(ln, rows, cols, "grey") == png
    scene3d._texture(ln, rows, cols[::2], "grey")
    assert len(calls) == 1
    np.testing.assert_array_equal(rg.data, before)
    ln.result = SimpleNamespace(analysis=rg.copy_with(data=-before))
    assert scene3d._texture(ln, rows, cols, "grey") != png
    assert len(calls) == 2


def test_report_figures_and_comparison_reuse_and_invalidate(saved_job, monkeypatch):
    from gpr_analyzer.report import figures
    job, _ = saved_job
    ln = job.line("L1")
    original, calls = figures.plt.subplots, []
    def counted(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(figures.plt, "subplots", counted)
    first = figures.render_line(ln.line_result())
    assert figures.render_line(ln.line_result()) == first
    assert len(calls) == 1
    ln.zones[0].review = "rejected"
    assert figures.render_line(ln.line_result()) != first
    assert len(calls) == 2
    counted_compare, compares = compare.compare_line, []
    def compare_call(*a, **kw):
        compares.append(1)
        return counted_compare(*a, **kw)
    monkeypatch.setattr(compare, "compare_line", compare_call)
    job.comparison_json(); job.comparison_json()
    assert len(compares) == 1
    job.comparison["tolerance"]["depth_m"] = 0.7
    job.comparison_json()
    assert len(compares) == 2


def test_job_cache_evicts_idle_arrays_but_keeps_active_identity_and_disk(tmp_path):
    store = JobStore(tmp_path, max_cached_jobs=1)
    first = store.new()
    first.save()
    identity = weakref.ref(first)
    jid = first.job_id
    second = store.new()
    second.save()
    assert store.get(jid) is first  # a request still holds the evicted object
    store.get(second.job_id)
    del first
    gc.collect()
    assert identity() is None
    assert (tmp_path / jid / "state.json").is_file()
    assert store.get(jid).job_id == jid


def test_preview_does_not_read_whole_companion(tmp_path, monkeypatch):
    p = tmp_path / "large.txt"
    p.write_text("header" * 10000)
    monkeypatch.setattr(type(p), "read_bytes", lambda *a: pytest.fail("read full file"))
    assert preview_text(p, 6) == "header"


def test_dielectric_map_uses_same_bistatic_display_depth():
    from gpr_analyzer.processing.velocity import HyperbolaFit
    fit = HyperbolaFit(0.5, 10, 0.1, 8, 0.1, 0.99)
    a = Radargram(np.zeros((30, 20)), 0.5, 0.1, metadata={"antenna_separation_m": 0.4})
    raw = a.copy_with(metadata={"gps": [{"trace": 0, "lat": 13, "lon": 100}, {"trace": 19, "lat": 13.001, "lon": 100}]})
    ln = SimpleNamespace(line_id="L1", raw=raw, sections=[],
                         result=SimpleNamespace(analysis=a, velocity_m_per_ns=0.1, velocity_source_code="user", hyperbola_fits=[fit]))
    point = geo.dielectric_points([ln])[0]["points"][0]
    assert point["depth_m"] == pytest.approx(float(depth_from_time(10, 0.1, 0.4)), abs=0.0005)
