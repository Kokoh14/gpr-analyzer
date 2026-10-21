"""Find the layout of an unknown binary GPR file (used for the SubScan format, clean-room).

Only our own data files are examined; no vendor code is read. The tool:
1. lists readable text in the file (header fields, device name, dates)
2. tries every combination of header size, per-trace header size, sample type, byte order and
   samples per trace that fits the file size exactly
3. scores each by how much neighbouring traces look alike (lateral coherence) and how smooth each
   trace is in time. A wrong type, byte order or trace length gives noise or diagonal shear.
4. optionally plots the best candidates so a person can confirm.

Usage: gpr-sniff FILE [--traces 555] [--plot out.png]
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram

DTYPES = ("<i2", ">i2", "<u2", ">u2", "<i4", ">i4", "<f4", ">f4", "i1", "u1")
TRACE_HEADERS = (0, 2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 128, 240)
COMMON_HEADERS = (0, 32, 64, 128, 256, 512, 1024, 2048, 3600, 4096)


@dataclass
class TextRun:
    offset: int
    text: str
    encoding: str


@dataclass
class Candidate:
    header_bytes: int
    trace_header_bytes: int
    dtype: str
    n_samples: int
    n_traces: int
    score: float = float("nan")
    lateral_corr: float = float("nan")
    smoothness: float = float("nan")
    periodicity: float = float("nan")

    @property
    def stride(self) -> int:
        return self.trace_header_bytes + self.n_samples * np.dtype(self.dtype).itemsize

    def describe(self) -> str:
        return (
            f"header={self.header_bytes:>6} trace_hdr={self.trace_header_bytes:>3} {self.dtype:>4} "
            f"samples={self.n_samples:>5} traces={self.n_traces:>6} score={self.score:6.3f} "
            f"(lateral={self.lateral_corr:5.3f}, smooth={self.smoothness:5.3f}, periodic={self.periodicity:5.3f})"
        )


def text_runs(raw: bytes, min_len: int = 4, limit: int = 200) -> list[TextRun]:
    """Printable ASCII/Latin-1 and UTF-16LE strings with their byte offsets."""
    out = []
    for m in re.finditer(rb"[\x20-\x7e\xc0-\xff]{%d,}" % min_len, raw):
        out.append(TextRun(m.start(), m.group().decode("latin-1"), "latin-1"))
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, raw):
        out.append(TextRun(m.start(), m.group().decode("utf-16-le"), "utf-16-le"))
    out.sort(key=lambda r: r.offset)
    return out[:limit]


def load(raw: bytes, c: Candidate, max_traces: int | None = None) -> np.ndarray:
    """Decode a candidate layout into (samples, traces) float64."""
    n = c.n_traces if max_traces is None else min(c.n_traces, max_traces)
    buf = np.frombuffer(raw, dtype=np.uint8, count=n * c.stride, offset=c.header_bytes)
    body = buf.reshape(n, c.stride)[:, c.trace_header_bytes :].copy()
    return body.view(c.dtype).astype(np.float64).T


def _pick_traces(n: int, k: int) -> np.ndarray:
    return np.unique(np.linspace(0, n - 2, min(k, n - 1)).astype(int))


def _corr_cols(a: np.ndarray, b: np.ndarray) -> float:
    """Median correlation between matching columns of a and b (columns with no energy ignored)."""
    den = np.sqrt((a * a).sum(0) * (b * b).sum(0))
    ok = den > 0
    return float(np.median((a * b).sum(0)[ok] / den[ok])) if ok.any() else -1.0


def quick_score(raw: bytes, c: Candidate, n: int = 16) -> float:
    """Cheap first-pass score: lateral coherence + smoothness on the first n traces."""
    with np.errstate(all="ignore"):
        d = load(raw, c, n)
        if not np.all(np.isfinite(d)) or np.abs(d).max() > 1e12:
            return -2.0
        d = d - d.mean(axis=0, keepdims=True)
        return _corr_cols(d[:, :-1], d[:, 1:]) + _corr_cols(d[:-1], d[1:])


def score_candidate(raw: bytes, c: Candidate, max_traces: int | None = None) -> Candidate:
    """Score by adjacent-trace correlation, penalising traces that repeat inside themselves
    (a sign that the trace length is a multiple of the true one).
    max_traces limits the work to the first traces (quick first pass)."""
    with np.errstate(all="ignore"):
        d = load(raw, c, max_traces)
    if not np.all(np.isfinite(d)) or np.abs(d).max() > 1e12:
        c.score = c.lateral_corr = c.smoothness = -1.0
        c.periodicity = 0.0
        return c
    d = d - d.mean(axis=0, keepdims=True)
    idx = _pick_traces(d.shape[1], 300)
    lateral = _corr_cols(d[:, idx], d[:, idx + 1])

    # GPR traces are oversampled, so consecutive samples are similar. Reading int16 data as int32
    # (or the reverse) keeps lateral coherence but breaks this.
    smooth = _corr_cols(d[:-1, idx], d[1:, idx])

    # Mean |trace| repeating with period n/k means k real traces were glued into one.
    env = np.abs(d).mean(axis=1)
    env = env - env.mean()
    periodic = 0.0
    if env.std() > 0:
        for k in (2, 3, 4):
            lag = c.n_samples // k
            if lag >= 8 and c.n_samples % k == 0:
                periodic = max(periodic, float(np.corrcoef(env[:-lag], env[lag:])[0, 1]))
    c.lateral_corr, c.smoothness, c.periodicity = lateral, smooth, periodic
    # Only a strong repeat counts; ordinary traces have some chance self-similarity.
    c.score = lateral + max(smooth, -1.0) - 2.5 * max(0.0, periodic - 0.6)
    return c


def candidates(
    size: int,
    n_traces: int | None = None,
    extra_headers: tuple[int, ...] = (),
    min_samples: int = 32,
    max_samples: int = 8192,
    max_header: int = 16384,
    min_traces: int = 10,
) -> list[Candidate]:
    """Layouts without per-trace headers that fit the file size exactly.

    A per-trace header only changes how a trace's bytes are split, not the trace stride, so trace
    headers are tried later (`expand_trace_headers`) on the strides that score best here.
    """
    out: list[Candidate] = []
    ns = np.arange(min_samples, max_samples + 1)
    headers = sorted({h for h in COMMON_HEADERS + tuple(extra_headers) if 0 <= h < size})
    for dtype in DTYPES:
        stride = ns * np.dtype(dtype).itemsize
        if n_traces:
            hdr = size - n_traces * stride
            for i in np.nonzero((hdr >= 0) & (hdr <= max_header))[0]:
                out.append(Candidate(int(hdr[i]), 0, dtype, int(ns[i]), n_traces))
            continue
        for h in headers:
            body = size - h
            fits = (body % stride == 0) & (body // stride >= min_traces)
            for i in np.nonzero(fits)[0]:
                out.append(Candidate(h, 0, dtype, int(ns[i]), int(body // stride[i])))
    return out


def expand_trace_headers(base: Candidate) -> list[Candidate]:
    """Every (type, per-trace header) split of the same header size and trace stride."""
    out = []
    for dtype in DTYPES:
        b = np.dtype(dtype).itemsize
        for th in TRACE_HEADERS:
            rest = base.stride - th
            if rest > 0 and rest % b == 0 and rest // b >= 16:
                out.append(Candidate(base.header_bytes, th, dtype, rest // b, base.n_traces))
    return out


def sniff(
    path: str | Path, n_traces: int | None = None, top: int = 10, n_strides: int = 15, **kw
) -> tuple[list[TextRun], list[Candidate]]:
    """Return (text found, best `top` layouts). Pass n_traces when it is known (e.g. 555 from the screen)."""
    raw = Path(path).read_bytes()
    runs = text_runs(raw)
    # The end of a text run near the start is a likely header end; try a few alignments.
    extra = set()
    for r in runs:
        if r.offset < 65536:
            end = r.offset + len(r.text) * (2 if r.encoding == "utf-16-le" else 1)
            for align in (1, 2, 4, 16, 128, 512, 1024):
                extra.add(-(-end // align) * align)
    base = candidates(len(raw), n_traces=n_traces, extra_headers=tuple(extra), **kw)
    # 1) quick score of plain layouts on the first traces; 2) try per-trace headers on the best strides.
    quick = sorted(base, key=lambda c: quick_score(raw, c), reverse=True)
    seen, strides = set(), []
    for c in quick:
        key = (c.header_bytes, c.stride)
        if key not in seen:
            seen.add(key)
            strides.append(c)
        if len(strides) >= n_strides:
            break
    expanded = [e for c in strides for e in expand_trace_headers(c)]
    full = sorted((score_candidate(raw, c) for c in expanded), key=lambda c: c.score, reverse=True)
    return runs, _resolve_ties(raw, full)[:top]


def _first_trace_fits(raw: bytes, c: Candidate) -> bool:
    """True when the first trace of the layout looks like its neighbour (i.e. is real data)."""
    with np.errstate(all="ignore"):
        d = load(raw, c, 3)
    d = d - d.mean(axis=0, keepdims=True)
    return _corr_cols(d[:, :1], d[:, 1:2]) >= 0.5 * c.lateral_corr


def _resolve_ties(raw: bytes, ranked: list[Candidate], tol: float = 0.02) -> list[Candidate]:
    """Layouts that only differ by skipping whole traces at the start (bigger file header) or the
    first samples of every trace (bigger per-trace header) score about the same as the true one.
    Among near-ties of the same type and stride, pick the smallest file header whose first trace is
    real data, then the smallest per-trace header."""
    if not ranked:
        return ranked
    best = ranked[0]
    ties = [c for c in ranked if c.score >= best.score - tol and (c.dtype, c.stride) == (best.dtype, best.stride)]
    ties.sort(key=lambda c: (c.header_bytes, c.trace_header_bytes))
    pick = next((c for c in ties if _first_trace_fits(raw, c)), ties[-1])
    return [pick] + [c for c in ranked if c is not pick]


def to_radargram(path: str | Path, c: Candidate, dt_ns: float = 1.0, **kw) -> Radargram:
    """Turn a chosen layout into a Radargram. dt_ns must come from the header or the screen."""
    raw = Path(path).read_bytes()
    meta = {"format": "sniffed", "source": str(path), "layout": c.__dict__.copy()}
    return Radargram(load(raw, c).astype(np.float32), dt_ns=dt_ns, metadata=meta, **kw)


def plot_candidates(path: str | Path, cands: list[Candidate], out: str | Path, n: int = 6) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    raw = Path(path).read_bytes()
    n = min(n, len(cands))
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 5), squeeze=False)
    for ax, c in zip(axes[0], cands[:n]):
        d = load(raw, c)
        d = d - d.mean(axis=1, keepdims=True)
        lim = np.percentile(np.abs(d), 98) or 1.0
        ax.imshow(d, aspect="auto", cmap="gray", vmin=-lim, vmax=lim)
        ax.set_title(f"{c.dtype} h={c.header_bytes} th={c.trace_header_bytes}\nns={c.n_samples} score={c.score:.2f}", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=100)
    plt.close(fig)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Guess the layout of an unknown binary GPR file.")
    p.add_argument("file")
    p.add_argument("--traces", type=int, help="known number of traces (e.g. read from the software screen)")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--plot", help="write a PNG of the best candidates")
    p.add_argument("--max-samples", type=int, default=8192)
    a = p.parse_args(argv)
    runs, best = sniff(a.file, n_traces=a.traces, top=a.top, max_samples=a.max_samples)
    print(f"File size: {Path(a.file).stat().st_size} bytes")
    print("Text found (offset: text):")
    for r in runs[:60]:
        print(f"  {r.offset:>8}: {r.text[:100]!r} [{r.encoding}]")
    print("Best layouts:")
    for c in best:
        print("  " + c.describe())
    if a.plot and best:
        plot_candidates(a.file, best, a.plot)
        print(f"Plot written to {a.plot}")


if __name__ == "__main__":
    main()
