"""Constant-velocity zero-offset migration: Stolt (f-k) and Kirchhoff (diffraction summation).

Both collapse diffraction hyperbolas back to their apex. Output keeps the input
grid; row i is migrated two-way time i*dt (depth = v t / 2). Run only after
background removal and with a velocity you trust: a wrong velocity smiles or
leaves hyperbola tails.
"""

from __future__ import annotations

import numpy as np


def stolt_migration(data: np.ndarray, dt_ns: float, dx_m: float, velocity_m_per_ns: float) -> np.ndarray:
    """Stolt f-k migration (exploding reflector, velocity v/2)."""
    n, m = data.shape
    ntp = 2 * n
    nxp = 2 * m
    vh = velocity_m_per_ns / 2.0
    P = np.fft.fft2(data.astype(np.float64), s=(ntp, nxp))
    w = 2 * np.pi * np.fft.fftfreq(ntp, d=dt_ns)  # rad/ns
    kx = 2 * np.pi * np.fft.fftfreq(nxp, d=dx_m)  # rad/m
    dz = vh * dt_ns
    kz = 2 * np.pi * np.fft.fftfreq(ntp, d=dz)
    order = np.argsort(w)
    ws = w[order]
    M = np.zeros_like(P)
    for j in range(nxp):
        kk = np.sqrt(kx[j] ** 2 + kz**2)
        w_map = np.sign(kz) * vh * kk
        col = P[order, j]
        re = np.interp(w_map, ws, col.real, left=0.0, right=0.0)
        im = np.interp(w_map, ws, col.imag, left=0.0, right=0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = np.where(kk > 0, np.abs(kz) / kk, 1.0)
        M[:, j] = (re + 1j * im) * scale
    out = np.real(np.fft.ifft2(M))[:n, :m]
    return out.astype(np.float32)


def kirchhoff_migration(
    data: np.ndarray,
    dt_ns: float,
    dx_m: float,
    velocity_m_per_ns: float,
    aperture_m: float | None = None,
) -> np.ndarray:
    """Diffraction-summation migration with obliquity weighting and a limited aperture."""
    n, m = data.shape
    d = data.astype(np.float64)
    if aperture_m is None:
        aperture_m = velocity_m_per_ns * n * dt_ns / 2.0  # max depth
    half = int(np.ceil(aperture_m / dx_m))
    t0 = np.arange(n) * dt_ns  # output two-way time
    out = np.zeros_like(d)
    v = velocity_m_per_ns
    for off in range(-half, half + 1):
        h = off * dx_m
        t = np.sqrt(t0**2 + 4.0 * h * h / (v * v))
        ti = t / dt_ns
        i0 = np.floor(ti).astype(int)
        frac = ti - i0
        ok = i0 + 1 < n
        with np.errstate(divide="ignore", invalid="ignore"):
            w = np.where(t > 0, t0 / t, 1.0)  # obliquity cos(theta)
        lo, hi = max(0, -off), min(m, m - off)
        if lo >= hi:
            continue
        src = d[:, lo + off : hi + off]
        samp = np.zeros((n, hi - lo))
        idx = i0[ok]
        samp[ok] = (1 - frac[ok, None]) * src[idx] + frac[ok, None] * src[idx + 1]
        out[:, lo:hi] += w[:, None] * samp
    return (out / (2 * half + 1)).astype(np.float32)
