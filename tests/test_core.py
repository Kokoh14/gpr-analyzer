import numpy as np
import pytest

from gpr_analyzer import Radargram


def test_axes():
    r = Radargram(np.zeros((100, 5)), dt_ns=0.5, dx_m=0.05, velocity_m_per_ns=0.1)
    assert r.data.dtype == np.float32
    assert r.n_samples == 100 and r.n_traces == 5
    assert r.time_axis()[-1] == pytest.approx(49.5)
    # depth = v * t / 2
    assert r.depth_axis()[-1] == pytest.approx(0.1 * 49.5 / 2)
    assert r.depth_axis(0.135)[10] == pytest.approx(0.135 * 5.0 / 2)
    assert r.distance_axis()[-1] == pytest.approx(0.2)


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        Radargram(np.zeros(10), dt_ns=1)
    with pytest.raises(ValueError):
        Radargram(np.zeros((3, 3)), dt_ns=0)


def test_copy_with_does_not_share_metadata():
    r = Radargram(np.zeros((4, 4)), dt_ns=1, metadata={"a": 1})
    r2 = r.copy_with(velocity_m_per_ns=0.12)
    r2.metadata["a"] = 2
    assert r.metadata["a"] == 1 and r2.velocity_m_per_ns == 0.12
