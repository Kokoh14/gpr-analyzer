import numpy as np
import pytest

from gpr_analyzer.processing.filters import envelope
from gpr_analyzer.processing.migration import kirchhoff_migration, stolt_migration
from gpr_analyzer.synthetic import single_hyperbola


def _focus(data, i0, j0):
    e = envelope(data).astype(np.float64) ** 2
    return e[i0 - 8 : i0 + 9, j0 - 5 : j0 + 6].sum() / e.sum()


@pytest.mark.parametrize("fn", [stolt_migration, kirchhoff_migration])
def test_migration_collapses_hyperbola(fn):
    rg, truth = single_hyperbola(velocity_m_per_ns=0.1, depth_m=1.5)
    i0 = int(round(truth.target_time_ns(truth.targets[0]) / rg.dt_ns))
    j0 = truth.target_trace(truth.targets[0], rg.dx_m)
    before = _focus(rg.data, i0, j0)
    out = fn(rg.data, rg.dt_ns, rg.dx_m, 0.1)
    assert out.shape == rg.data.shape
    after = _focus(out, i0, j0)
    assert before < 0.2
    assert after > 0.85
    # wrong velocity focuses much worse: this is what makes migration a velocity check
    assert _focus(fn(rg.data, rg.dt_ns, rg.dx_m, 0.13), i0, j0) < after - 0.3
    i, j = np.unravel_index(np.argmax(envelope(out)), out.shape)
    assert abs(j - j0) <= 2 and abs(i - i0) <= 2
