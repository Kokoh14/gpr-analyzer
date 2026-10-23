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
