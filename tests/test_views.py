import pytest

from gpr_analyzer.processing.auto import run_pipeline
from gpr_analyzer.processing.views import ENVELOPE_NOTE, VIEWS, render_views, save_views
from gpr_analyzer.synthetic import make_scene


@pytest.fixture(scope="module")
def result():
    rg, _ = make_scene(n_traces=150, n_samples=256, artifact_traces=[60])
    return run_pipeline(rg)


def test_render_three_views_with_legends(result):
    figs = render_views(result)
    assert tuple(figs) == VIEWS
    env_fig = figs["envelope"]
    labels = [ax.get_ylabel() for ax in env_fig.axes]
    assert "Envelope (dB re. max)" in labels
    texts = [t.get_text() for ax in env_fig.axes for t in ax.texts]
    assert ENVELOPE_NOTE in texts
    assert any("Depth (m)" in ax.get_ylabel() for ax in figs["grey"].axes for ax in [ax] + list(ax.child_axes))
    assert "display only" in figs["high_gain"].axes[0].get_title()


def test_save_views(result, tmp_path):
    paths = save_views(result, tmp_path, stem="L1")
    assert set(paths) == set(VIEWS)
    for p in paths.values():
        assert p.exists() and p.stat().st_size > 5000
