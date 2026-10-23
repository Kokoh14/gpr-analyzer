import numpy as np
import pytest

from gpr_analyzer.processing import filters as F
from gpr_analyzer.synthetic import make_scene, ricker


def _sine(n=512, dt=0.25, f_mhz=250.0):
    t = np.arange(n) * dt
    return t, np.sin(2 * np.pi * f_mhz * 1e-3 * t)


def test_center_frequency_of_ricker():
    rg, _ = make_scene(ringing_ns=None, artifact_traces=[], wow_amplitude=0.0)
    fc = F.estimate_center_frequency(rg.data, rg.dt_ns)
    # Ricker peak frequency is the nominal; the half-power centroid sits a little above it
    assert 230 <= fc <= 300


def test_dewow_removes_drift_keeps_band():
    t, s = _sine()
    drift = 2.0 * np.exp(-t / 60.0)
    x = (s + drift)[:, None].astype(np.float32)
    out = F.dewow(x, 0.25, window_ns=8.0)[:, 0]
    mid = slice(50, 450)
    assert abs(out[mid].mean()) < 0.05
    assert np.corrcoef(out[mid], s[mid])[0, 1] > 0.98


def test_first_break_and_time_zero_alignment():
    n, m, dt = 300, 40, 0.25
    rng = np.random.default_rng(1)
    tz = 10.0 + rng.uniform(-1.5, 1.5, m)
    t = np.arange(n)[:, None] * dt
    d = (ricker(t - tz[None, :], 250.0) + 0.3 * ricker(t - tz[None, :] - 30.0, 250.0)).astype(np.float32)
    picks = F.first_break_picks(d, 0.3, search_samples=16)
    assert np.max(np.abs(picks * dt - tz)) < 0.1
    target = int(round(np.median(picks)))
    out = F.time_zero_correct(d, picks, target)
    # direct wave now at row 0, reflector at 30 ns on every trace
    assert np.all(np.argmax(out[:20], axis=0) == 0)
    assert np.all(np.abs(np.argmax(out[60:], axis=0) + 60 - 30.0 / dt) <= 1)


def test_bandpass_rejects_out_of_band():
    t, s = _sine(f_mhz=250.0)
    _, lo = _sine(f_mhz=20.0)
    _, hi = _sine(f_mhz=1500.0)
    x = (s + lo + hi)[:, None]
    out = F.bandpass(x, 0.25, 100.0, 500.0)[:, 0]
    mid = slice(60, 450)
    assert np.corrcoef(out[mid], s[mid])[0, 1] > 0.99


def test_bandpass_bad_band():
    with pytest.raises(ValueError):
        F.bandpass(np.zeros((10, 2)), 0.25, 500.0, 100.0)


@pytest.mark.parametrize("statistic", ["mean", "median"])
def test_background_removal_kills_flat_band_keeps_local_event(statistic):
    n, m = 200, 300
    d = np.zeros((n, m), np.float32)
    d[50] = 1.0  # flat band on every trace
    d[120, 140:160] = 1.0  # local event
    out = F.background_removal(d, window_traces=101, statistic=statistic)
    assert np.abs(out[50]).max() < 1e-5
    assert out[120, 150] > 0.75


def test_background_median_leaves_no_ghost():
    d = np.zeros((100, 300), np.float32)
    d[40, 140:160] = 1.0
    ghost_mean = np.abs(F.background_removal(d, 101, "mean")[40, :130]).max()
    ghost_median = np.abs(F.background_removal(d, 101, "median")[40, :130]).max()
    assert ghost_mean > 0.1
    assert ghost_median == 0.0


def test_envelope_of_sine_is_flat():
    t, s = _sine()
    env = F.envelope(s[:, None])[50:-50, 0]
    assert np.allclose(env, 1.0, atol=0.05)


def test_sec_gain_compensates_decay_and_caps_at_noise():
    dt, n = 0.5, 600
    t = np.arange(n) * dt
    rate = 0.3  # dB/ns
    decay = 10 ** (-rate * t / 20.0) + 1e-3
    nf = F.estimate_noise_floor(decay)
    gain, cap, rate_est = F.sec_gain_curve(decay, dt, nf, max_gain_db=80.0)
    assert abs(rate_est - rate) < 0.05
    # cap lands where the signal meets 2x noise: 10^(-0.3 t/20) = 1e-3 -> t = 200 ns
    assert 150 <= cap * dt <= 200
    assert np.all(gain[cap:] == gain[cap])
    assert np.all(np.diff(gain[: cap + 1]) >= 0)


def test_sec_gain_respects_max():
    decay = 10 ** (-np.arange(1000) * 0.25 * 0.5 / 20.0)
    gain, _, _ = F.sec_gain_curve(decay, 0.25, noise_floor=1e-12, max_gain_db=20.0)
    assert gain.max() <= 10.0 + 1e-9


def test_agc_equalises_and_stabiliser_quiets_noise():
    rng = np.random.default_rng(0)
    d = rng.standard_normal((400, 10))
    d[:200] *= 100.0
    out = F.agc(d, 0.25, 10.0)
    assert 0.5 < np.std(out[250:]) / np.std(out[:150]) < 2.0
    quiet = F.agc(d, 0.25, 10.0, noise_rms=5.0)
    assert np.std(quiet[250:]) < 0.3 * np.std(quiet[:150])
