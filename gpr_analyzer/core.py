"""Shared data model used by every subpackage."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np


def depth_from_time(t_ns, velocity_m_per_ns: float, separation_m: float = 0.0):
    """Reflector depth (m) for two-way time t_ns measured from the direct ground wave.

    Transmitter and receiver are separation_m apart; at t = 0 the wave has only crossed the
    separation, so depth is 0. Negative times give negative depths (v t / 2).
    """
    t = np.asarray(t_ns, dtype=np.float64)
    h = velocity_m_per_ns * t / 2.0
    if not separation_m:
        return h
    s = separation_m / 2.0
    return np.where(h > 0, np.sqrt(np.maximum((h + s) ** 2 - s**2, 0.0)), h)


def time_from_depth(depth_m, velocity_m_per_ns: float, separation_m: float = 0.0):
    """Inverse of depth_from_time."""
    d = np.asarray(depth_m, dtype=np.float64)
    s = separation_m / 2.0
    h = np.where(d > 0, np.sqrt(d**2 + s**2) - s, d)
    return 2.0 * h / velocity_m_per_ns


def trace_bounds(n: int, dx_m: float | None, start_m: float | None, end_m: float | None) -> tuple[int, int]:
    """Inclusive trace indices for a distance range, shared by eager and ranged readers."""
    if not dx_m:
        raise ValueError("this file has no trace spacing, so a distance range cannot be applied")
    t0 = 0 if start_m is None else int(np.floor(max(start_m, 0.0) / dx_m))
    t1 = n - 1 if end_m is None else int(np.ceil(end_m / dx_m))
    t0, t1 = max(t0, 0), min(t1, n - 1)
    if t1 - t0 < 10:
        raise ValueError(f"range {start_m}–{end_m} m leaves fewer than 10 traces (line is {n * dx_m:.1f} m)")
    return t0, t1


def crop_metadata(metadata: dict[str, Any], t0: int, t1: int, n: int, dx_m: float) -> dict[str, Any]:
    """Shift navigation/markers to a range, retaining GPS anchors just outside both ends."""
    meta = dict(metadata)
    gps = sorted(meta.get("gps") or [], key=lambda r: r["trace"])
    if gps:
        inside = [i for i, r in enumerate(gps) if t0 <= r["trace"] <= t1]
        lo = max((inside[0] if inside else next((i for i, r in enumerate(gps) if r["trace"] > t1), len(gps))) - 1, 0)
        hi = min((inside[-1] if inside else lo) + 1, len(gps) - 1)
        meta["gps"] = [{**r, "trace": r["trace"] - t0} for r in gps[lo:hi + 1]]
    if meta.get("markers"):
        meta["markers"] = [{**m, "trace": m["trace"] - t0} for m in meta["markers"] if t0 <= m.get("trace", -1) <= t1]
    if meta.get("dead_traces"):
        meta["dead_traces"] = [t - t0 for t in meta["dead_traces"] if t0 <= t <= t1]
    meta["crop"] = {"trace_start": t0, "trace_end": t1, "start_m": round(t0 * dx_m, 3),
                    "end_m": round(t1 * dx_m, 3), "n_traces_file": n}
    return meta


def crop_to_range(rg: "Radargram", start_m: float | None, end_m: float | None) -> tuple["Radargram", int]:
    t0, t1 = trace_bounds(rg.n_traces, rg.dx_m, start_m, end_m)
    return rg.copy_with(data=np.ascontiguousarray(rg.data[:, t0:t1 + 1]),
                        metadata=crop_metadata(rg.metadata, t0, t1, rg.n_traces, rg.dx_m)), t0


@dataclass
class Radargram:
    """One GPR profile (B-scan).

    data: float32 array, shape (samples, traces). Row = time sample, column = trace.
    dt_ns: sample interval in nanoseconds.
    dx_m: trace spacing in metres, or None when the file has no distance info.
    velocity_m_per_ns: EM wave velocity used for time-to-depth conversion.
    metadata: free-form header values from the reader (antenna, frequency, source file...).
    """

    data: np.ndarray
    dt_ns: float
    dx_m: float | None = None
    velocity_m_per_ns: float = 0.1
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data = np.asarray(self.data, dtype=np.float32)
        if self.data.ndim != 2:
            raise ValueError(f"data must be 2-D (samples, traces), got shape {self.data.shape}")
        if self.dt_ns <= 0:
            raise ValueError("dt_ns must be positive")

    @property
    def n_samples(self) -> int:
        return self.data.shape[0]

    @property
    def n_traces(self) -> int:
        return self.data.shape[1]

    def time_axis(self) -> np.ndarray:
        """Two-way travel time of each sample, in ns."""
        return np.arange(self.n_samples, dtype=np.float64) * self.dt_ns

    def depth_axis(self, velocity_m_per_ns: float | None = None) -> np.ndarray:
        """Depth of each sample in metres (row 0 = time zero).

        Without antenna separation this is v * t / 2. With metadata["antenna_separation_m"] = S the
        bistatic geometry is used, row 0 being the direct ground wave (depth 0):
        depth = sqrt((v t / 2 + S / 2)^2 - (S / 2)^2). This is what ViewPoint's depth axis shows.
        """
        v = self.velocity_m_per_ns if velocity_m_per_ns is None else velocity_m_per_ns
        return depth_from_time(self.time_axis(), v, self.metadata.get("antenna_separation_m") or 0.0)

    def distance_axis(self) -> np.ndarray:
        """Position of each trace along the line in metres, or trace index if dx_m is unknown."""
        step = 1.0 if self.dx_m is None else self.dx_m
        return np.arange(self.n_traces, dtype=np.float64) * step

    def copy_with(self, **changes: Any) -> "Radargram":
        """Return a new Radargram with some fields replaced (metadata is copied)."""
        changes.setdefault("metadata", dict(self.metadata))
        return replace(self, **changes)
