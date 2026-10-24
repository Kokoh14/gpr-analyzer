# Depth-coloured thick slice (#18)

The review screen has a separate **Depth-coloured thick slice** panel. Select top/bottom
depth, contrast in dB, and GPS or the local layout already saved in the 3D panel, then render.
North is up and east is right. Bounds are shown in metres; GPS scenes report a common local origin.

Each 0.2 m map cell retains the strongest analysis-envelope return inside the inclusive depth range.
Its actual sample depth sets the colour (yellow → green → blue); amplitude relative to the survey
maximum sets brightness over the selected dB range. Each line uses its own velocity and antenna
separation. Header time zero is respected when automatic time-zero correction is disabled.

No values are interpolated across lines or empty map cells. Transparent cells are unsurveyed or
have zero return. GPS positions interpolate between fixes, without extrapolation and without
bridging wheel intervals above 5 m. Reviewer-excluded sections are omitted. Local layouts remain
user-supplied straight-line assumptions. Grid size is limited to 2 million cells / 4096 per side;
larger extents ask for a smaller survey rather than silently coarsening or exhausting memory.

Acceptance: `tests/test_thick_slice.py` renders four synthetic lines with returns at 0.5 m and
1.5 m into one PNG and asserts distinct pixel colours, transparent gaps, strongest-at-crossings,
contrast, range, exclusion, nonlinear depth, GPS coverage and API validation. Map/3D regressions
also pass. This is a display aid, not detector validation or a calibrated field result.
