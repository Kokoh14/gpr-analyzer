# Positioning QA (#17)

Before processing, the folder table shows GPS points per wheel metre, traces per wheel metre,
and first/last usable GPS timestamps. The map selector colours consecutive measured-fix segments
by either density. Missing spacing remains unknown; GPS never replaces wheel distance.

A wheel gap above 5 m warns of sparse GPS sampling (not proof of a GPS outage).
A heading change of at least 120 degrees warns of a possible swath overlap. This is a heuristic:
the headers do not provide the swath width needed to establish an outline self-intersection.
Zero-quality, duplicate and nonfinite fixes do not contribute to the diagnostics.
Existing positioning/interpolation in processed maps is unchanged; the wider GPS audit remains separate.

Acceptance: `tests/test_positioning.py` compares a clean synthetic line against a sparse line
with a 180 degree turn, checks local density, timestamps and unknown spacing.
Folder/API regression tests exercise the integration. No field calibration is claimed.
