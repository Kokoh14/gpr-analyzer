"""Signal processing steps (dewow, time-zero, bandpass, background removal, gain, ...).

Entry point: run_pipeline(radargram) -> ProcessingResult with an amplitude-preserving
analysis track, an AGC display track, envelope/energy, velocity and a parameter log.
"""

from gpr_analyzer.processing.auto import ProcessingOptions, ProcessingResult, StepLog, run_pipeline
from gpr_analyzer.processing.velocity import HyperbolaFit, estimate_velocity, fit_hyperbola

__all__ = [
    "HyperbolaFit",
    "ProcessingOptions",
    "ProcessingResult",
    "StepLog",
    "estimate_velocity",
    "fit_hyperbola",
    "run_pipeline",
]
