"""Synthetic survey for trying the app without real files.

Writes two ImpulseRadar .iprh/.iprb pairs (plus a .txt note) so the demo goes through the
same upload path as real data from the user's main unit. Line 1 is a 555-trace,
~145 ns line with vertical stripes with pipes, a dipping layer and an air-filled
cavity; line 2 is a quieter line with two pipes.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram
from gpr_analyzer.synthetic.generator import Layer, PointTarget, make_scene, ricker


def add_cavity(rg: Radargram, x0: float, x1: float, top_m: float, bottom_m: float, amp: float = 3.0,
               time_zero_ns: float = 6.0, attenuation_db_per_ns: float = 0.25, freq_mhz: float = 250.0) -> None:
    """Air-filled cavity between x0 and x1 (m): reversed-polarity roof, ringing inside, edge diffractions."""
    v = rg.velocity_m_per_ns
    t = rg.time_axis()[:, None]
    x = rg.distance_axis()[None, :]
    inside = (x >= x0) & (x <= x1)
    t_top, t_bot = 2 * top_m / v, 2 * bottom_m / v
    sub = np.zeros(rg.data.shape)
    for k, a in enumerate((1.0, 0.6, 0.36, 0.2)):
        sub -= amp * a * ricker(t - time_zero_ns - t_top - k * (t_bot - t_top) / 4, freq_mhz) * inside
    sub += 0.5 * amp * ricker(t - time_zero_ns - t_bot, freq_mhz) * inside
    for xe in (x0, x1):
        r = np.sqrt(top_m**2 + (x - xe) ** 2)
        sub -= 0.6 * amp * (top_m / r) * ricker(t - time_zero_ns - 2 * r / v, freq_mhz) * ~inside
    sub *= 10.0 ** (-attenuation_db_per_ns * np.clip(t - time_zero_ns, 0, None) / 20.0)
    rg.data += sub.astype(np.float32)


def demo_lines() -> list[tuple[str, Radargram]]:
    v = 0.12
    rg1, _ = make_scene(velocity_m_per_ns=v, seed=1,
                        targets=[PointTarget(4.0, 1.0), PointTarget(23.5, 2.0)],
                        layers=[Layer(5.5, 0.3, 0.01)])
    add_cavity(rg1, 10.0, 12.5, 2.0, 3.0)
    rg2, _ = make_scene(n_traces=400, velocity_m_per_ns=v, seed=2, artifact_traces=[150],
                        targets=[PointTarget(6.0, 0.8), PointTarget(14.0, 1.6, 0.8)],
                        layers=[Layer(3.0, 0.35, -0.02)], ringing_ns=None)
    return [("DEMO_LINE01", rg1), ("DEMO_LINE02", rg2)]


IPRH = """HEADER VERSION: 20
DATA VERSION: 32
DATE: 2026-10-21
ANTENNA: 250 MHz (synthetic)
SAMPLES: {samples}
FREQUENCY: {fs:.6f}
TIMEWINDOW: {window:.3f}
LAST TRACE: {traces}
DISTANCE INTERVAL: {dx:.6f}
USER DISTANCE INTERVAL: {dx:.6f}
OPERATOR COMMENT: synthetic demo line, not field data
"""


def write_impulseradar(rg: Radargram, stem: Path) -> list[Path]:
    """Write rg as an int32 ImpulseRadar pair stem.iprh + stem.iprb."""
    scale = 2.0e9 / max(float(abs(rg.data).max()), 1e-12) / 4
    hdr = stem.with_suffix(".iprh")
    hdr.write_text(IPRH.format(samples=rg.n_samples, fs=1000.0 / rg.dt_ns, window=rg.n_samples * rg.dt_ns,
                               traces=rg.n_traces, dx=rg.dx_m or 0.0), encoding="latin-1")
    dat = stem.with_suffix(".iprb")
    np.round(rg.data.T * scale).astype("<i4").tofile(dat)
    return [hdr, dat]


# Made-up GPS start point and heading per demo line (two parallel passes along a street in Bangkok).
DEMO_GPS = {"DEMO_LINE01": (13.738600, 100.531000, 80.0), "DEMO_LINE02": (13.738520, 100.531020, 80.0)}


def write_cor(rg: Radargram, path: Path, lat0: float, lon0: float, heading_deg: float, every: int = 10) -> Path:
    """ImpulseRadar-style .cor GPS file: trace, date, time, lat, N/S, lon, E/W, elevation, quality 4 = RTK fixed (one fix per `every` traces)."""
    dx = rg.dx_m or 0.05
    m_lat = 111_320.0
    m_lon = m_lat * np.cos(np.radians(lat0))
    h = np.radians(heading_deg)
    rows = []
    for i, tr in enumerate(range(0, rg.n_traces, every)):
        d = tr * dx
        lat = lat0 + d * np.cos(h) / m_lat + 2e-7 * np.sin(i / 3.0)  # small GPS wobble
        lon = lon0 + d * np.sin(h) / m_lon
        rows.append(f"{tr}\t2026-10-21\t09:00:{i % 60:02d}.000\t{lat:.8f}\tN\t{lon:.8f}\tE\t2.10\tM\t4")
    path.write_text("\n".join(rows) + "\n", encoding="latin-1")
    return path


def write_demo_files(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for stem, rg in demo_lines():
        paths += write_impulseradar(rg, folder / stem)
        paths.append(write_cor(rg, folder / f"{stem}.cor", *DEMO_GPS[stem]))
    note = folder / "DEMO_LINE01.txt"
    note.write_text("Synthetic demo line (not field data)\nAntenna: 250 MHz (simulated)\n"
                    "Traces: 555, time window 145 ns, trace spacing 0.05 m\n", encoding="utf-8")
    paths.append(note)
    return paths


def write_fence_demo_files(folder: Path) -> list[Path]:
    """Eight synthetic passes, including reversed and crossing lines, for the 3D viewer."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(8):
        rg, _ = make_scene(n_traces=161, n_samples=320, time_window_ns=100, dx_m=0.1,
                           velocity_m_per_ns=0.1, seed=40 + i, artifact_traces=[], ringing_ns=None,
                           targets=[PointTarget(5 + i * 0.2, 1.2), PointTarget(11, 2.2)],
                           layers=[Layer(3.2, 0.45, 0.01)])
        stem = folder / f"SYNTHETIC_GRID_{i + 1:02d}"
        paths += write_impulseradar(rg, stem)
        if i < 6:
            x, y, heading = (0, i * 2, 90) if i % 2 == 0 else (16, i * 2, 270)
        else:
            x, y, heading = 4 + (i - 6) * 8, -3, 0
        paths.append(write_cor(rg, stem.with_suffix(".cor"), 13.7386 + y / 111320,
                               100.531 + x / (111320 * np.cos(np.radians(13.7386))), heading))
    return paths


def write_volume_demo_files(folder: Path) -> list[Path]:
    """Nine parallel eastward passes 0.5 m apart for the 3D volume: a pipe at 6 m along every line
    (1.0 m deep) and an air-filled cavity at 10–12 m on passes 4–6 only (1.2–1.8 m deep)."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(9):
        rg, _ = make_scene(n_traces=161, n_samples=320, time_window_ns=100, dx_m=0.1,
                           velocity_m_per_ns=0.1, seed=70 + i, artifact_traces=[], ringing_ns=None,
                           targets=[PointTarget(6.0, 1.0)], layers=[Layer(3.0, 0.4, 0.0)])
        if 3 <= i <= 5:
            add_cavity(rg, 10.0, 12.0, 1.2, 1.8, time_zero_ns=6.0)
        stem = folder / f"SYNTHETIC_VOLUME_{i + 1:02d}"
        paths += write_impulseradar(rg, stem)
        paths.append(write_cor(rg, stem.with_suffix(".cor"), 13.7386 + i * 0.5 / 111320, 100.531, 90.0))
    return paths
