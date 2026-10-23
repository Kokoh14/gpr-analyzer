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

All tracks share the grid: row 0 = time zero (ground surface), `velocity_m_per_ns` = the
pipeline's velocity, `metadata["track"]` names the track.

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

`gpr_analyzer.synthetic.make_scene()` builds a 555-trace, 145 ns line like the SubScan
sample with known truth: point diffractors, dipping layer, direct wave with time-zero
jitter, ringing band at 35 ns, vertical-stripe artifacts at traces 200/240/260/310/365–385,
wow, attenuation and noise. `single_hyperbola()` gives a clean diffraction for velocity and
migration tests.
