"""File readers. Every reader returns a list of Radargram (one per channel).

Use `read(path)` to pick the reader from the file extension.
"""

from __future__ import annotations

from pathlib import Path

from gpr_analyzer.core import Radargram
from gpr_analyzer.io.gssi import read_dzt
from gpr_analyzer.io.impulseradar import read_cor, read_impulseradar, read_mala
from gpr_analyzer.io.segy import read_segy, write_segy
from gpr_analyzer.io.sensoft import read_dt1

READERS = {
    ".iprh": read_impulseradar,
    ".iprb": read_impulseradar,
    ".dzt": read_dzt,
    ".rad": read_mala,
    ".rd3": read_mala,
    ".rd7": read_mala,
    ".hd": read_dt1,
    ".dt1": read_dt1,
    ".sgy": read_segy,
    ".segy": read_segy,
}

SUPPORTED_EXTENSIONS = tuple(sorted(READERS))


def read(path: str | Path) -> list[Radargram]:
    """Read any supported GPR file. Raises ValueError for unknown extensions
    (for an unknown binary format, use gpr_analyzer.io.sniff)."""
    path = Path(path)
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(
            f"{path.name}: unsupported file type {path.suffix!r}; supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    return reader(path)


read_file = read

__all__ = [
    "read",
    "read_file",
    "read_impulseradar",
    "read_mala",
    "read_cor",
    "read_dzt",
    "read_dt1",
    "read_segy",
    "write_segy",
    "READERS",
    "SUPPORTED_EXTENSIONS",
]
