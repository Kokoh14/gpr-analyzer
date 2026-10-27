"""Report figures: processed section (greyscale) and energy map (colour) with zone boxes.

Axes follow the report rule of three location references: distance (m) at the bottom,
trace number at the top, two-way time (ns) on the left, depth (m) on the right.
"""

from __future__ import annotations

import io
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from gpr_analyzer.report.schema import LineResult  # noqa: E402
from gpr_analyzer.core import depth_from_time, time_from_depth  # noqa: E402

ACTION_COLOURS = {"E": "#b0002a", "1": "#e8590c", "2": "#f2b705", "3": "#2f80ed", "none": "#7d8590"}


def envelope(data: np.ndarray) -> np.ndarray:
    """Instantaneous amplitude along time (axis 0) via the analytic signal."""
    from scipy.signal import hilbert

    return np.abs(hilbert(np.asarray(data, dtype=np.float64), axis=0))


def render_line(line: LineResult, view: str = "gray", actions: dict[str, str] | None = None,
                width_in: float = 7.0, height_in: float = 3.2, dpi: int = 150) -> bytes:
    """PNG bytes of one line. view = "gray" (processed amplitude) or "envelope" (colour energy)."""
    r = line.radargram
    # Caller keeps the cache only for the lifetime of this immutable processing result.
    key = json.dumps([line.to_dict(), actions or {}, width_in, height_in, dpi], sort_keys=True, default=str) if line.figure_cache is not None else None
    previous = line.figure_cache.get(view) if line.figure_cache is not None else None
    if previous is not None and previous[0] is r and previous[1] == key:
        return previous[2]
    data = np.asarray(r.data, dtype=np.float64)
    n_s, n_t = data.shape
    t_max = n_s * r.dt_ns
    actions = actions or {}

    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
    extent = (-0.5, n_t - 0.5, t_max, 0)
    if view == "envelope":
        img = envelope(data)
        vmax = np.percentile(img, 99.5) or 1.0
        im = ax.imshow(img, aspect="auto", cmap="turbo", extent=extent, vmin=0, vmax=vmax,
                       interpolation="bilinear")
        cb = fig.colorbar(im, ax=ax, pad=0.09, fraction=0.035)
        cb.set_label("envelope (rel.)", fontsize=7)
        cb.ax.tick_params(labelsize=6)
        box_c, txt_c = "white", "white"
    else:
        clip = np.percentile(np.abs(data), 99) or 1.0
        ax.imshow(data, aspect="auto", cmap="gray", extent=extent, vmin=-clip, vmax=clip,
                  interpolation="bilinear")
        box_c, txt_c = None, "black"

    for a in line.artifact_regions:
        t0 = 0 if a.time_top_ns is None else a.time_top_ns
        t1 = t_max if a.time_bottom_ns is None else a.time_bottom_ns
        ax.add_patch(Rectangle((a.trace_start - 0.5, t0), a.trace_end - a.trace_start + 1, t1 - t0,
                               fill=False, hatch="////", edgecolor="#9aa0a6", linewidth=0.0, alpha=0.9))

    for z in line.zones:
        if z.review == "rejected":
            continue
        colour = box_c or ACTION_COLOURS.get(actions.get(z.zone_id, "none"), "#7d8590")
        style = ":" if z.is_artifact else "-"
        ax.add_patch(Rectangle((z.trace_start - 0.5, z.time_top_ns), z.trace_end - z.trace_start + 1,
                               z.time_bottom_ns - z.time_top_ns, fill=False, edgecolor=colour,
                               linewidth=1.4, linestyle=style))
        ax.text(z.trace_start, z.time_top_ns - t_max * 0.012, z.zone_id, color=txt_c if box_c else colour,
                fontsize=7, fontweight="bold", va="bottom", ha="left",
                bbox=None if box_c else dict(facecolor="white", edgecolor="none", pad=0.6, alpha=0.8))

    ax.set_ylabel("two-way time (ns)", fontsize=7)
    ax.tick_params(labelsize=6)
    # bottom: distance; top: trace number
    if line.trace_spacing_m:
        dx = line.trace_spacing_m
        ax.set_xlabel("distance along line (m)", fontsize=7)
        ticks = ax.get_xticks()
        ticks = ticks[(ticks >= 0) & (ticks <= n_t - 1)]
        ax.set_xticks(ticks)
        ax.set_xticklabels([f"{t * dx:.1f}" for t in ticks])
        top = ax.secondary_xaxis("top")
        top.set_xlabel("trace", fontsize=7)
        top.tick_params(labelsize=6)
    else:
        ax.set_xlabel("trace", fontsize=7)
    v = line.velocity_m_per_ns
    sep = line.antenna_separation_m
    right = ax.secondary_yaxis("right", functions=(lambda t: depth_from_time(t, v, sep),
                                                  lambda d: time_from_depth(d, v, sep)))
    right.set_ylabel(f"depth (m), v = {v:.3f} m/ns", fontsize=7)
    right.tick_params(labelsize=6)
    ax.set_xlim(-0.5, n_t - 0.5)
    ax.set_ylim(t_max, 0)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    png = buf.getvalue()
    if line.figure_cache is not None:
        line.figure_cache[view] = (r, key, png)
    return png


def render_overview_map(tracks: dict[str, tuple[np.ndarray, np.ndarray]], zones: list[tuple[object, str]],
                        width_in: float = 6.0, dpi: int = 150) -> bytes | None:
    """Plan view of the GPS tracks (lat, lon per trace) with zones coloured by action level.

    Positions are drawn in metres east/north of the first track's first fix (local equirectangular
    projection, ample for a site a few km across). zones = [(Zone, action)] with WGS84 coord_start/end
    as (lon, lat). Returns None when no line has GPS.
    """
    tracks = {k: v for k, v in tracks.items() if v is not None and len(v[0])}
    if not tracks:
        return None
    lat0, lon0 = (float(a[0]) for a in next(iter(tracks.values())))
    r = 6371008.8
    kx, ky = np.radians(1.0) * r * np.cos(np.radians(lat0)), np.radians(1.0) * r

    def xy(lat, lon):
        return (np.asarray(lon, dtype=float) - lon0) * kx, (np.asarray(lat, dtype=float) - lat0) * ky

    fig, ax = plt.subplots(figsize=(width_in, width_in * 0.75), dpi=dpi)
    for line_id, (lat, lon) in tracks.items():
        x, y = xy(lat, lon)
        ax.plot(x, y, color="#4a5560", linewidth=1.0)
        ax.plot(x[0], y[0], marker="o", markersize=3, color="#4a5560")
        ax.annotate(line_id, (x[0], y[0]), fontsize=7, color="#4a5560", xytext=(3, 3), textcoords="offset points")
    for z, act in zones:
        if z.coord_start is None:
            continue
        colour = ACTION_COLOURS.get(act, "#7d8590")
        (x0, y0), (x1, y1) = (xy(c[1], c[0]) for c in (z.coord_start, z.coord_end or z.coord_start))
        ax.plot([x0, x1], [y0, y1], color=colour, linewidth=4, solid_capstyle="round", alpha=0.85)
        ax.annotate(f"{z.line_id}:{z.zone_id}", ((x0 + x1) / 2, (y0 + y1) / 2), fontsize=6.5, color=colour,
                    fontweight="bold", xytext=(4, -8), textcoords="offset points")
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("east (m)", fontsize=7)
    ax.set_ylabel("north (m)", fontsize=7)
    ax.tick_params(labelsize=6)
    ax.grid(True, linewidth=0.3, color="#c8ccd0")
    ax.set_title(f"origin (0, 0) = {lat0:.6f}, {lon0:.6f} (lat, lon WGS84)", fontsize=7, loc="left")
    for act, colour in ACTION_COLOURS.items():
        if any(a == act for _, a in zones):
            ax.plot([], [], color=colour, linewidth=4, label=f"action {act}")
    if zones:
        ax.legend(fontsize=6, loc="best", framealpha=0.85)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()
