"""Multi-colour radargram views: greyscale, high gain, and envelope colour map with legend.

Every figure has trace/distance on x, two-way time on the left axis and depth
(at the pipeline's velocity) on the right, and a caption naming the track it
shows, so a colour can never be read without its meaning.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from gpr_analyzer.core import Radargram  # noqa: E402

VIEWS = ("grey", "high_gain", "envelope", "viewpoint")

ENVELOPE_NOTE = "High envelope = strong reflection, not by itself a void"


def _clip(data: np.ndarray, pct: float) -> float:
    c = float(np.percentile(np.abs(data), pct))
    return c if c > 0 else 1.0


def _base(rg: Radargram, title: str, figsize=(10, 4.5)):
    fig = Figure(figsize=figsize, dpi=110, layout="constrained")
    ax = fig.add_subplot(1, 1, 1)
    x = rg.distance_axis()
    t = rg.time_axis()
    extent = (x[0], x[-1] if x.size > 1 else 1.0, t[-1] if t.size > 1 else 1.0, t[0])
    ax.set_xlabel("Distance (m)" if rg.dx_m is not None else "Trace")
    ax.set_ylabel("Two-way time (ns)")
    v = rg.velocity_m_per_ns
    sec = ax.secondary_yaxis("right", functions=(lambda tt: tt * v / 2.0, lambda d: 2.0 * d / v))
    sec.set_ylabel(f"Depth (m) at v = {v:.3f} m/ns")
    ax.set_title(title, fontsize=10)
    return fig, ax, extent


def grey_view(rg: Radargram, title: str = "Processed (amplitude-preserving)", clip_pct: float = 99.0) -> Figure:
    """Classic greyscale wiggle-density view, symmetric around zero."""
    fig, ax, extent = _base(rg, title)
    c = _clip(rg.data, clip_pct)
    ax.imshow(rg.data, cmap="gray", vmin=-c, vmax=c, aspect="auto", extent=extent, interpolation="bilinear")
    return fig


def high_gain_view(rg: Radargram, title: str = "High gain (AGC, display only)", clip_pct: float = 95.0) -> Figure:
    """Strong gain to show weak deep events. Amplitudes are not comparable across the image."""
    fig, ax, extent = _base(rg, title)
    c = _clip(rg.data, clip_pct)
    im = ax.imshow(rg.data, cmap="seismic", vmin=-c, vmax=c, aspect="auto", extent=extent, interpolation="bilinear")
    cb = fig.colorbar(im, ax=ax, pad=0.08, shrink=0.9)
    cb.set_label("Polarity (AGC-normalised)")
    return fig


def envelope_view(
    rg: Radargram,
    title: str = "Reflection strength (Hilbert envelope)",
    floor_db: float = -30.0,
    cmap: str = "turbo",
) -> Figure:
    """Envelope in dB relative to the section maximum, with a labelled colour bar."""
    fig, ax, extent = _base(rg, title)
    env = np.abs(rg.data).astype(np.float64)
    ref = env.max() if env.size and env.max() > 0 else 1.0
    db = 20.0 * np.log10(np.maximum(env / ref, 10 ** (floor_db / 20.0)))
    im = ax.imshow(db, cmap=cmap, vmin=floor_db, vmax=0.0, aspect="auto", extent=extent, interpolation="bilinear")
    cb = fig.colorbar(im, ax=ax, pad=0.08, shrink=0.9)
    cb.set_label("Envelope (dB re. max)")
    ax.text(
        0.01, -0.22, ENVELOPE_NOTE, transform=ax.transAxes, fontsize=8, style="italic", va="top"
    )
    return fig


def viewpoint_view(rg: Radargram, title: str = "ViewPoint-like (display only)", clip_pct: float = 97.0) -> Figure:
    """Greyscale like the ImpulseRadar field screen: depth on the left (antenna-separation aware)."""
    fig = Figure(figsize=(10, 4.5), dpi=110, layout="constrained")
    ax = fig.add_subplot(1, 1, 1)
    x = rg.distance_axis()
    z = rg.depth_axis()
    extent = (x[0], x[-1] if x.size > 1 else 1.0, z[-1] if z.size > 1 else 1.0, z[0])
    c = _clip(rg.data, clip_pct)
    # depth is not linear in samples near the surface; resample rows onto an even depth grid
    zz = np.linspace(z[0], z[-1], rg.n_samples)
    rows = np.interp(zz, z, np.arange(rg.n_samples))
    img = np.array([np.interp(rows, np.arange(rg.n_samples), col) for col in rg.data.T]).T
    ax.imshow(img, cmap="gray", vmin=-c, vmax=c, aspect="auto", extent=extent, interpolation="bilinear")
    ax.set_xlabel("Distance (m)" if rg.dx_m is not None else "Trace")
    ax.set_ylabel(f"Depth (m) at v = {rg.velocity_m_per_ns:.3f} m/ns")
    ax.set_title(title, fontsize=10)
    return fig


def render_views(result) -> dict[str, Figure]:
    """Figures for a ProcessingResult: grey (analysis), high_gain (display track), envelope, viewpoint."""
    figs = {
        "grey": grey_view(result.analysis),
        "high_gain": high_gain_view(result.display),
        "envelope": envelope_view(result.envelope),
    }
    if getattr(result, "viewpoint", None) is not None:
        figs["viewpoint"] = viewpoint_view(result.viewpoint)
    return figs


def save_views(result, out_dir: str | Path, stem: str = "line", fmt: str = "png") -> dict[str, Path]:
    """Write each view to out_dir/<stem>_<view>.<fmt>; returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, fig in render_views(result).items():
        p = out / f"{stem}_{name}.{fmt}"
        fig.savefig(p)
        plt.close(fig)
        paths[name] = p
    return paths
