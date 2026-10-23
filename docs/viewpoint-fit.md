# Matching ImpulseRadar ViewPoint on real data

Outside-in fit of what the ViewPoint field app shows, from a real CrossOver 4080 survey
(78 lines × 2 channels, 400 / 800 MHz, ViewPoint 1.4.18). Inputs were only the raw
`.iprh/.iprb/.mrk/.cor` files, ViewPoint's 18 marker screenshots (`Export/AutoScreenShots`)
and its field-summary PDF. No vendor code was read. The survey is not in this repository.

Re-run on any ViewPoint survey folder:

```bash
pip install pillow
python scripts/fit_viewpoint_screens.py "<survey folder>" --out scores.csv
GPR_REAL_DATA="<survey folder>" pytest tests/test_real_impulseradar.py
```

## How screenshots are lined up

Each screenshot is `<line>.<marker>.jpg`, both channels stacked. The filled marker circle is drawn
at the marker's `(trace, sample)` from the line's `.mrk`, which pins one point of each panel; the
labelled 5 m ticks give pixels per metre; pixels per sample is searched (1–4 px) for the best
row-by-row correlation. All 32 usable panels landed on 2.00 or 2.49 px/sample (two zoom levels),
the same for both channels, which is a good sign the registration is right.

## What was found

| Item | ViewPoint behaviour | Evidence |
|---|---|---|
| Trace spacing | `DISTANCE INTERVAL` (0.018615 m), not `USER DISTANCE INTERVAL` (0.020 m) | traces × 0.018615 = `STOP POSITION` on every line; screenshot ticks agree |
| Time zero | header `ZERO LEVEL` (sample 68 at 800 MHz, 42 at 400 MHz), one shift for all traces | depth-0 tick sits at that sample; 800 MHz direct wave moves < 0.6 sample on all 76 lines |
| Depth | `sqrt((v t/2 + S/2)² − (S/2)²)`, t from `ZERO LEVEL`, v = `SOIL VELOCITY` (100 m/µs = 0.1 m/ns), S = `ANTENNA SEPARATION` | depth ticks within 0.5 sample on all screenshots; field-PDF marker depths 0.36/0.57/0.56/0.57 m reproduced exactly, 0.63/0.68 m come out 1 cm deeper. Plain `v t/2` is 3–4 cm short |
| Background removal | running mean over ≈1.9 m (101 traces) | best on 31 of 32 panels against none, global mean/median, 11–401 trace windows and sliding median |
| Dewow / band-pass | dewow window barely matters; an extra band-pass makes the match worse | |
| Gain | grows with time then flattens: `G (1 − e^(−t/τ))` dB. 800 MHz: G = 58 dB, τ = 20 ns. 400 MHz: G = 32 dB, τ = 16 ns | row-by-row fit, RMSE 1.4 / 1.8 dB; a pure exponential misfits by 3.7–3.9 dB. Screen contrast is user-adjustable in ViewPoint, so these are this survey's settings |

These are the defaults of the new `viewpoint` track (`processing/viewpoint.py`). The `analysis`
track keeps its amplitude-preserving SEC gain and 30 m background window, since detection
scores on it; it now also takes time zero from `ZERO LEVEL`.

## Match before and after

Mean over panels, `r_row` = row-by-row correlation with the screen (ignores gain),
`grey RMSE` = screen grey levels (0–255) after mapping our amplitudes to grey with the best contrast.

| Track | 800 MHz r_row | 800 MHz grey RMSE | 400 MHz r_row | 400 MHz grey RMSE |
|---|---|---|---|---|
| `display` (AGC, previous display output) | 0.55 | 31.3 | 0.40 | 45.0 |
| `analysis` (SEC, 30 m background) | 0.59 | 30.8 | 0.52 | 43.6 |
| `viewpoint` (this fit) | **0.73** | **24.8** | **0.65** | **35.7** |

The ceiling is limited by JPEG compression, black/white clipping on strong layers, and panels
whose marker circle covers the feature. Lines 58 (400 MHz) score lowest: most of the panel is
clipped by a strong metallic target.

## Reader and processing fixes found on the way

- `USER DISTANCE INTERVAL` was preferred over the measured step: distances were 7 % too long.
- `.cor` GPS of a dual-channel line is `<line>.cor`, shared by `_0` and `_1`; it was never found.
- `.mrk` markers (trace, sample, colour, position) are now read into `metadata["markers"]`.
- Empty `.iprb` files (two in this survey) now raise a clear error instead of a reshape error.
- The first trace of most lines is all zero; listed in `metadata["dead_traces"]`.
- Direct-wave picking used |amplitude|; on 400 MHz the positive and negative lobes are nearly
  equal and picks jumped about 7 samples trace to trace, which the per-trace alignment turned
  into vertical stripes. Picks now stay on one polarity (spread 7.0 → 1.8 samples, median line).

## Not the same data as the earlier screenshots

Earlier screenshots discussed in the project (555 traces, 0–145 ns, ~9.5 m, 0.135 m/ns) do not
come from this survey: here each line has 1100 samples, 0–107 ns (800 MHz) or 0–215 ns (400 MHz),
and ViewPoint used 0.1 m/ns. Those numbers still need their own raw files to check.
