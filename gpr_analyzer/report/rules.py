"""Report rules: provisional action level and wording checks.

The action matrix here is a placeholder until the collapse-risk scoring work is merged; every
report that uses it says so. Timeframes per action level are set per client, not here.
"""

from __future__ import annotations

import re

from gpr_analyzer.report.schema import ACTION_LEVELS, Zone

# (suspected_type, confidence) -> action for road/building, and for open ground.
_VOID_MATRIX = {
    "confirmed": ("E", "1"),
    "high": ("1", "2"),
    "medium": ("2", "3"),
    "low": ("3", "3"),
}


def suggest_action(zone: Zone, surface_use: str = "unknown") -> str:
    """Action level for a zone. A level already set on the zone (by user or scorer) wins."""
    if zone.action is not None:
        return zone.action
    if zone.review == "rejected" or zone.suspected_type in ("artifact", "layer", "pipe_point"):
        return "none"
    if zone.suspected_type == "void_like":
        busy, open_ = _VOID_MATRIX[zone.confidence]
        return open_ if surface_use == "open" else busy
    # scattered / unknown: watch only when the grade is at least medium
    return "3" if zone.confidence in ("confirmed", "high", "medium") else "none"


def action_rank(action: str) -> int:
    """0 = most urgent (E)."""
    return ACTION_LEVELS.index(action)


_FORBIDDEN = [
    (re.compile(r"\b(void|cavity|cave)s?\s+(was\s+|were\s+|is\s+)?(found|detected|present|confirmed)\b", re.I),
     "claims a void without ground truth; say 'void-like zone' / 'anomaly'"),
    (re.compile(r"(พบ|เจอ|มี)โพรง"), "อ้างว่าพบโพรงโดยไม่มีผลพิสูจน์ ใช้ 'โซนคล้ายโพรง' / 'โซนผิดปกติ'"),
    (re.compile(r"\d\s*%|%\s*\d|เปอร์เซ็นต์|percent", re.I), "uncalibrated percentage"),
]


def find_wording_violations(text: str) -> list[str]:
    """Return a message per forbidden phrase found (empty list = text is fine)."""
    out = []
    for pattern, why in _FORBIDDEN:
        for m in pattern.finditer(text):
            out.append(f"{m.group(0)!r}: {why}")
    return out
