"""Helpers shared by the readers."""

from __future__ import annotations

from pathlib import Path

C_M_PER_NS = 0.299792458


def parse_key_value_header(text: str, sep: str = ":") -> dict[str, str]:
    """Parse 'KEY<sep>value' lines into an upper-cased-key dict. Lines without sep are skipped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if sep not in line:
            continue
        key, value = line.split(sep, 1)
        key = key.strip().upper()
        if key:
            out[key] = value.strip()
    return out


def read_text(path: Path) -> str:
    """Read a header text file that may be in any 8-bit encoding."""
    return Path(path).read_bytes().decode("latin-1")


def to_float(value: str | None) -> float | None:
    """First number in a header value ('400 MHz' -> 400.0), or None."""
    if value is None:
        return None
    token = value.strip().split()
    if not token:
        return None
    try:
        return float(token[0].replace(",", "."))
    except ValueError:
        return None


def sibling(path: Path, suffix: str) -> Path:
    """Return the file next to `path` with another extension, matching case if possible."""
    path = Path(path)
    for cand in (path.with_suffix(suffix.lower()), path.with_suffix(suffix.upper())):
        if cand.exists():
            return cand
    for cand in path.parent.glob(path.stem + ".*"):
        if cand.suffix.lower() == suffix.lower():
            return cand
    raise FileNotFoundError(f"no {suffix} file next to {path}")
