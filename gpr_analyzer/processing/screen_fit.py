"""Score our tracks against ImpulseRadar ViewPoint marker screenshots (clean-room, outside-in).

ViewPoint's "AutoScreenShots" are named <line>.<marker>.jpg and show both channels: 800 MHz on
top, 400 MHz below a blue divider, a 0-5-10 m distance axis on top and a filled marker circle
drawn at the marker's (trace, sample) from the line's .mrk file. That gives every panel an exact
anchor; the horizontal scale comes from the labelled 5 m ticks and the vertical scale
(pixels per sample) is found by maximising the row-by-row correlation with our data.

Scores per panel (gain-independent and gain-dependent):
- row_corr: mean over screen rows of Pearson r between screen grey and our amplitude
  (unsaturated pixels only), so a different gain curve does not change it;
- grey_rmse / grey_corr: our track mapped to grey as clip(128 + k x) with the best k,
  compared with the screen as a whole, so the gain curve does count.

Needs Pillow (pip install pillow); used by scripts/fit_viewpoint_screens.py and the real-data tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

PANEL_CHANNEL = {0: 0, 1: 1}  # top panel = channel 0 (800 MHz), bottom = channel 1 (400 MHz)


@dataclass
class Panel:
    channel: int
    grey: np.ndarray  # screen rows x columns (data area only)
    valid: np.ndarray  # False on marker circles, labels, outside the line
    y0: int
    x0: int
    circle_yx: tuple[float, float] | None
    px_per_m: float | None


def load_screenshot(path: str | Path) -> list[Panel]:
    """Split a ViewPoint marker screenshot into its two channel panels."""
    from PIL import Image

    im = np.asarray(Image.open(path).convert("RGB")).astype(float)
    h, w, _ = im.shape
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    grey = im.mean(2)
    blue = (b - r > 60) & (b - g > 30)
    brows = np.where(blue[:, 200 : w - 80].mean(1) > 0.8)[0]
    axrows = np.where((grey[:, 100 : w - 10] < 60).mean(1) > 0.9)[0]
    if brows.size == 0 or axrows.size == 0:
        raise ValueError(f"{path}: not a two-channel ViewPoint screenshot")
    ax = axrows[0]
    top = axrows[-1] + 1
    while top < brows[0] and grey[top, 90 : w - 10].std() < 2:  # scroll bar rows are flat
        top += 1
    band = grey[ax - 6 : ax - 1, 80:]
    t = np.where((band < 100).mean(0) > 0.6)[0] + 80
    ticks = [int(v) for i, v in enumerate(t) if i == 0 or v - t[i - 1] > 2]
    if len(ticks) >= 2:
        pxm = float(np.median(np.diff(ticks))) / 5.0
    elif len(ticks) == 1:
        pxm = (ticks[0] - 75.5) / 5.0  # only "5.0" visible; 0 m is on the left axis
    else:
        pxm = None
    colored = (np.abs(r - g) > 35) | (np.abs(r - b) > 35) | (np.abs(g - b) > 35)
    bad = ndimage.binary_dilation(colored, iterations=4)
    x0 = 77
    out = []
    for ch, (y0, y1) in {0: (top + 2, brows[0] - 2), 1: (brows[-1] + 3, h - 2)}.items():
        lab, n = ndimage.label(ndimage.binary_closing(colored[y0:y1, x0:], iterations=2))
        best = None
        for k in range(1, n + 1):
            ys, xs = np.where(lab == k)
            if ys.size < 600:
                continue
            if 0.7 < (np.ptp(ys) + 1) / (np.ptp(xs) + 1) < 1.4 and (best is None or ys.size > best[0]):
                best = (ys.size, ys.mean(), xs.mean())
        out.append(Panel(ch, grey[y0:y1, x0:], ~bad[y0:y1, x0:], y0, x0,
                         None if best is None else (best[1], best[2]), pxm))
    return out


def _sample(arr, rows, cols):
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    return ndimage.map_coordinates(arr.astype(np.float64), [rr, cc], order=1, mode="constant", cval=np.nan)


def _row_corr(grey, ours, valid, min_px=40):
    cs = []
    for i in range(grey.shape[0]):
        m = valid[i] & np.isfinite(ours[i])
        if m.sum() < min_px:
            continue
        gg, pp = grey[i, m], ours[i, m]
        u = (gg > 8) & (gg < 247)
        if u.sum() < min_px or pp[u].std() == 0 or gg[u].std() == 0:
            continue
        cs.append(np.corrcoef(gg[u], pp[u])[0, 1])
    return float(np.mean(cs)) if cs else float("nan")


@dataclass
class Registration:
    marker_trace: float
    marker_sample: float  # in raw samples of this channel
    px_per_trace: float
    px_per_sample: float
    circle_yx: tuple[float, float]

    def grid(self, panel: Panel) -> tuple[np.ndarray, np.ndarray]:
        """(raw sample per screen row, trace per screen column)."""
        cy, cx = self.circle_yx
        rows = self.marker_sample + (np.arange(panel.grey.shape[0]) - cy) / self.px_per_sample
        cols = self.marker_trace + (np.arange(panel.grey.shape[1]) - cx) / self.px_per_trace
        return rows, cols


def register(panel: Panel, ref: np.ndarray, marker: tuple[int, int], dx_m: float,
             px_per_sample_range=(1.0, 4.0)) -> tuple[Registration, float]:
    """Find pixels-per-sample for a panel. ref: raw-sample-indexed array (samples x traces)."""
    if panel.circle_yx is None or not panel.px_per_m:
        raise ValueError("panel has no marker circle or distance ticks")
    reg = Registration(marker[0], marker[1], panel.px_per_m * dx_m, 0.0, panel.circle_yx)
    valid0 = panel.valid.copy()

    def score(a):
        reg.px_per_sample = a
        rows, cols = reg.grid(panel)
        v = valid0 & ((cols >= 0) & (cols <= ref.shape[1] - 1))[None, :]
        return _row_corr(panel.grey, _sample(ref, rows, cols), v)

    lo, hi = px_per_sample_range
    grid = np.linspace(lo, hi, 61)
    s = [score(a) for a in grid]
    a0 = grid[int(np.nanargmax(s))]
    fine = np.linspace(a0 - 0.06, a0 + 0.06, 25)
    s = [score(a) for a in fine]
    reg.px_per_sample = float(fine[int(np.nanargmax(s))])
    return reg, float(np.nanmax(s))


def compare(panel: Panel, reg: Registration, track: np.ndarray, zero_sample: float) -> dict[str, float]:
    """Scores of a time-zeroed track (row 0 = raw sample zero_sample) against a registered panel."""
    rows, cols = reg.grid(panel)
    ours = _sample(track, rows - zero_sample, cols)
    v = panel.valid & np.isfinite(ours) & ((cols >= 0) & (cols <= track.shape[1] - 1))[None, :]
    rc = _row_corr(panel.grey, ours, v)
    g, x = panel.grey[v], ours[v]
    scale = np.percentile(np.abs(x), 99) or 1.0
    best = (np.inf, 0.0, 0.0)
    for k in np.geomspace(10, 2000, 80):
        sim = np.clip(128 + k * x / scale, 0, 255)
        e = float(np.sqrt(np.mean((sim - g) ** 2)))
        if e < best[0]:
            best = (e, k, float(np.corrcoef(sim, g)[0, 1]))
    return {"row_corr": rc, "grey_rmse": best[0], "grey_corr": best[2]}


__all__ = ["load_screenshot", "register", "compare", "Panel", "Registration"]
