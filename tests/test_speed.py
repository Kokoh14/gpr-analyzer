"""The speed-ups must not change any result: same layer mask, same zones, same tracks."""

import numpy as np

from gpr_analyzer.detect.zones import _row_runs_at_least, long_reflectors


def _runs_reference(mask, length):  # the original per-row loop
    out = np.zeros_like(mask)
    for i in np.flatnonzero(mask.any(axis=1)):
        d = np.diff(np.concatenate(([0], mask[i].astype(np.int8), [0])))
        for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
            if b - a >= length:
                out[i, a:b] = True
    return out


def _long_reference(mask, length, max_dip, n_slopes=None):  # the original shear with full index arrays
    n_s, n_t = mask.shape
    out = np.zeros_like(mask)
    x = np.arange(n_t)
    if n_slopes is None:
        n_slopes = int(np.ceil(2 * max_dip * length / 1.5)) + 1
    if not mask.any():
        return out
    for slope in np.linspace(-max_dip, max_dip, n_slopes):
        shift = np.round(slope * (x - n_t / 2)).astype(int)
        rows = np.arange(n_s)[:, None] + shift[None, :]
        valid = (rows >= 0) & (rows < n_s)
        sheared = np.zeros_like(mask)
        sheared[np.where(valid, rows, 0), x[None, :].repeat(n_s, 0)] |= mask & valid
        thick = sheared | np.roll(sheared, 1, axis=0) | np.roll(sheared, -1, axis=0)
        long_ = _runs_reference(thick, length) & sheared
        out |= long_[np.where(valid, rows, 0), x[None, :].repeat(n_s, 0)] & valid & mask
    return out


def test_long_reflectors_match_the_original_algorithm():
    rng = np.random.default_rng(1)
    for trial in range(150):
        n_s, n_t = int(rng.integers(1, 90)), int(rng.integers(1, 120))
        mask = rng.random((n_s, n_t)) < rng.choice([0.03, 0.2, 0.5, 0.9])
        for _ in range(3):  # straight dipping reflectors
            r0, dip = rng.integers(0, n_s), rng.uniform(-0.3, 0.3)
            for x in range(n_t):
                if 0 <= int(r0 + dip * x) < n_s:
                    mask[int(r0 + dip * x), x] = True
        length = int(rng.integers(1, max(2, n_t)))
        dip = float(rng.choice([0.0, 0.05, 0.2, 0.6]))
        assert np.array_equal(_row_runs_at_least(mask, length), _runs_reference(mask, length)), trial
        assert np.array_equal(long_reflectors(mask, length, dip), _long_reference(mask, length, dip)), trial


def test_lines_in_parallel_give_the_same_result(tmp_path, monkeypatch):
    from gpr_analyzer.app import jobs
    from gpr_analyzer.app.demo import write_demo_files

    files = write_demo_files(tmp_path / "in")
    done = {}
    for workers in (1, 2):
        monkeypatch.setattr(jobs, "line_workers", lambda n, w=workers: min(n, w))
        folder = tmp_path / f"job{workers}"
        folder.mkdir()
        job = jobs.Job(job_id=f"j{workers}", folder=folder)
        steps = []
        step = job._step
        monkeypatch.setattr(job, "_step", lambda e, s: (steps.append(s), step(e, s)))
        job.load(files)
        assert not job.problems and len(job.lines) >= 2
        done[workers] = (job, steps)
    (a, sa), (b, sb) = done[1], done[2]
    assert "ประมวลผลทีละเส้น" not in (b.folder / "log.txt").read_text(encoding="utf-8")  # the worker pool did start
    assert sorted(sa) == sorted(sb)  # every processing step still reaches the progress table
    for la, lb in zip(a.lines, b.lines):
        assert la.line_id == lb.line_id
        for track in ("analysis", "envelope", "display", "viewpoint", "autodisplay"):
            assert np.array_equal(getattr(la.result, track).data, getattr(lb.result, track).data)
        assert [z.to_dict() for z in la.zones] == [z.to_dict() for z in lb.zones]
        assert la.artifact_regions == lb.artifact_regions
