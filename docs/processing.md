# Processing pipeline

`gpr_analyzer.processing.auto_process(radargram, options=None)` is the CLI/report hook: it
returns `(analysis Radargram, steps)` where steps are `report.schema.ProcessingStep` dicts
(`name`, `params` incl. `reason`, `auto`), and sets `metadata["velocity_source"]` to a report
code (`hyperbola_fit` / `file_header` / `default` / `user`).

`gpr_analyzer.processing.run_pipeline(radargram, options=None)` runs every step with
values chosen from the data, and records each choice (value + reason) in
`result.log` / `result.parameter_table()` for the report. Any value can be overridden
through `ProcessingOptions`.

| # | Step | Default (data-driven) |
|---|---|---|
| 1 | Centre frequency | Half-power centroid of the mean spectrum (> 20 MHz) |
| 2 | Dewow | Subtract a Gaussian-smoothed trace, window = 2 periods |
| 3 | Time zero | Direct-wave peak per trace (sub-sample), aligned to the median, samples above cut |
| 4 | Bandpass | Butterworth 4, zero-phase, 0.4×–2× centre frequency |
| 5 | Background removal | Sliding **median** over 30 m (global if line shorter / no dx). Median avoids flat ghosts of hyperbola apexes. The window was 10 m until the end-to-end test showed it erased a 7.5 m flat void roof: a median removes anything flat over more than half its window |
| 6 | Gain (SEC) | Exponential, rate fitted to the 90th-percentile envelope decay; held constant from where signal meets 2× noise floor; max 60 dB |
| 7 | Velocity | x²–t² hyperbola fits (median of accepted fits); falls back to header, then 0.1 m/ns and says so |
| 8 | Migration | Off by default; `stolt` or `kirchhoff` |
| 9 | Attributes | Hilbert envelope; RMS energy over 2 periods |
| 10 | Display AGC | Window 10 periods, stabilised at 5× noise RMS |

## Tracks in `ProcessingResult`

- `analysis` – steps 1–6, amplitude-preserving. **Detection and scoring use this.**
- `layers` – as `analysis` but without background removal (keeps flat reflectors / layers).
- `envelope`, `energy` – attributes of `analysis`.
- `display` – AGC. For the eye only; amplitudes are not comparable.
- `migrated` – only when requested.
- `viewpoint` – ViewPoint-like screen (`docs/viewpoint-fit.md`). Display only.
- `autodisplay` – automatic display filters chosen per line (below). Display only;
  `metadata["display_clip"]` is the colour-scale limit chosen with them.

All tracks share the grid: row 0 = time zero (ground surface), `velocity_m_per_ns` = the
pipeline's velocity, `metadata["track"]` names the track.

## Automatic display filters (`autodisplay`)

`processing/autodisplay.py`. Neither ViewPoint, Condor nor GPR-SLICE chooses filters
automatically (`docs/reference-manuals.md`); here every display parameter is measured on the
line itself and logged as `display_*` steps with the numbers behind it, so the app's recipe
table shows what was chosen and why.

The chain branches off a copy of the time-zeroed, dewowed data (after step 3). It never
feeds the analysis track, detection or scoring; `tests/test_autodisplay.py` checks that every
other track is bit-for-bit the same with and without it.

| Step | Rule | Why |
|---|---|---|
| Dead traces | All-zero traces are left out of the background statistics and stay blank | A zero trace would otherwise show the background as a stripe |
| Band-pass | Power spectra of the signal part (below the direct wave, down to where the 90th-percentile envelope meets 2× the noise floor) and of the noise-only part below it. Pass band = the frequencies around the spectral peak where signal is ≥ 6 dB above noise, searched from 0.3× the centre frequency, never narrower than the signal's half-power band. No noise-only part (reflections reach the end of the window) → 0.4×–2× centre frequency, said so | Removes only what is measured to be noise; a band narrower than the wavelet's own makes it ring |
| Background removal | Rows where the line's median trace holds ≥ half the energy are the ringing times. On those rows only, sliding medians of whole line, 30, 20, 15, 10, 7.5, 5, 3, 2 m are scored by the banding they leave: per row, the median over 2 m blocks of the block-median energy (so a flat event must cover most of the line to count). The widest window leaving < 5 % (or ≤ 2× the narrowest's) is used | Wide windows keep flat reflectors such as cavity roofs; ringing that changes along the line (surface change) still gets a shorter window. ViewPoint's 1.9 m mean flattens a 7.5 m roof (test) |
| Gain | Inverse of the 90th-percentile envelope decay, made non-increasing first, held constant below where it meets 2× the noise floor, max 60 dB (`gain_max_db`). One curve for the whole line | Equalises depth without boosting noise; unlike AGC, strengths at the same depth stay comparable along the line, and a strong deep layer is not turned down |
| Contrast | Clip at the 99th percentile of \|amplitude\| below the direct wave | Only the strongest 1 % saturates |

Limits: the 6 dB, 5 %, 2 m block and 2 m minimum window are fixed rules, not fitted to field
truth. A flat reflector that runs along most of a line at a ringing time cannot be told apart
from ringing (the `layers` track keeps it). Not yet checked on the real ImpulseRadar survey:
`scripts/fit_viewpoint_screens.py` now also scores `autodisplay` against ViewPoint screenshots.

## Velocity from hyperbolas

`processing.velocity.estimate_velocity`: candidate apexes are local envelope maxima
(normalised per trace so full-depth stripes do not dominate) → velocity/position scan →
pick → fit t² = t0² + 4(x−x0)²/v² (linear least squares) → re-pick and refit.
A fit is accepted only if both limbs are sampled, RMS residual < 0.1 period, R² > 0.9, the
event is ≥ 4× the section's median envelope and it is not a duplicate/crossing of a
stronger fit. A fit gives velocity, apex and depth – never pipe radius.

## Views

`processing.views.render_views(result)` / `save_views(result, dir)`:

- `grey` – analysis track, greyscale.
- `high_gain` – display (AGC) track, red/blue polarity, titled "display only".
- `envelope` – envelope in dB re. max, `turbo` colour map with labelled colour bar and the
  note "High envelope = strong reflection, not by itself a void".

Each figure shows distance, two-way time and depth at the pipeline velocity.

## Synthetic data

`gpr_analyzer.synthetic.make_scene()` builds a 555-trace, 145 ns line like an early
sample with known truth: point diffractors, dipping layer, direct wave with time-zero
jitter, ringing band at 35 ns, vertical-stripe artifacts at traces 200/240/260/310/365–385,
wow, attenuation and noise. `single_hyperbola()` gives a clean diffraction for velocity and
migration tests.
