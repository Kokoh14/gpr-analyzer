# detect/ — zones, artifact flags, rule-based scores, collapse-risk grade

```python
from gpr_analyzer.detect import analyze_line, RiskContext

res = analyze_line(processed_radargram,                      # amplitude-preserving track, no AGC
                   velocity_m_per_ns=v, velocity_source="hyperbola_fit",   # optional, from processing
                   risk_context=RiskContext(material="rock", consequence=2))  # optional site info
res.to_json()            # one JSON record per line (schema "gpr-analyzer/detection", version 1)
res.zones[0].summary_th()
# โซน A trace 124–325, ลึก 5.0–7.5 ม. | โซนคล้ายโพรง: ปานกลาง ✓ สะท้อนแรง ✓ ... ⚠ แถบแนวตั้ง trace 365–385 ...
```

`detect_zones(r, line_id="L1")` returns `(zones, artifact_regions)` as dicts in the
`gpr_analyzer.report.schema` layout (the CLI hook).

## Steps

| Step | Module | What it does |
|---|---|---|
| Artifacts | `artifacts.py` | Vertical stripes: traces whose energy differs from ±25 neighbours in ≥ 50 % of 8 depth windows. Dead, clipped and stationary (operator stopped, ≥ 6 near-identical traces) traces. Bottom noise: below the last laterally coherent sample (dip-tolerant correlation), where the noise envelope keeps rising — i.e. gain applied to noise. Ringing: time rows (≤ 3 periods thick) where ≥ 15 % of the traces are bright only in short pieces (< 15 traces), spread over at least 4 of 5 parts of the line — the speckled residue of antenna ringing after background removal. Inside a ringing band the contrast is scaled down so only what stands ≥ 2× above the band's 90th percentile stays bright. |
| Contrast | `_signal.py` | Hilbert envelope ÷ background envelope at the same time (median over traces, running median over ~8 periods). Works on gained and ungained data. |
| Hyperbolas | `hyperbola.py` | Apex candidates (local maxima, one per bright plateau plus the plateau's two ends, moved to the earliest arrival), limbs followed outward inside the window allowed by v = 0.03–0.20 m/ns relative to the previous pick, x²–t² least-squares fit, R² ≥ 0.9 and real moveout. One-limb fits are kept (cavity edges, line ends) unless the event continues upward on the other side. Apexes on artifact traces or in bottom noise are skipped. Velocity = median of fits after outlier rejection. |
| Zones | `zones.py` | Threshold contrast ≥ 4, cut hyperbola limbs beyond the apex core, split off straight reflectors ≥ half the line long (dip up to 0.3 samples/trace, slopes tried finely enough that a dipping layer stays within ±1 row), close gaps, never cross the bottom-noise onset. Narrow artifact bands (≤ 5 traces) are bridged by interpolation, wider ones masked. A component wider than half the line, or holding two full hyperbola apexes more than 2 periods apart in time, is cut back to its brighter parts (threshold raised step by step). Boxes merge when they overlap; boxes holding a full apex only when the overlap is ≥ 30 % of the smaller box (otherwise boxes around crossing limbs chain along the whole line). Boxes without an apex narrower than the first Fresnel radius at their depth, √(λd/2 + λ²/16), are dropped: nothing that narrow is a reflector of its own. Bounding boxes → `Zone` with trace / time / depth / distance / width / thickness. |
| Classify | `classify.py` | Features per zone, then one rule set per type (below). Same level → artifact first, then the type with more of its own signs found. |
| Risk | `risk.py` | Void-like zones only: collapse-risk grade I–IV (see below). |
| Cross-line | `crossline.py` | After all lines of a job: each suspicious zone is looked for on neighbouring lines by GPS (see below). |

## Classification rules (no percentages)

Evidence status: `found` ✓, `absent` ✗, `warning` ⚠ (nearby artifact), `not_assessed` – (could not check).

| Type | Signs | High | Medium | Low |
|---|---|---|---|---|
| Pipe / point (`point_target`) | hyperbola apex in zone, single central apex, zone no wider than its limbs | all three | central apex | any apex |
| Layer | continuous roof (coverage ≥ 80 %, residual ≤ 1 period, or traced as one long reflector), wide (≥ 30 traces or 1/5 line), thin (≤ 3 periods), no two-limb apex | continuous + wide + thin | continuous + thin, or a traced long reflector that is wide | continuous |
| Void-like | strong, bounded; **specific:** roof polarity reversed vs the direct wave, diffractions at both edges, evenly spaced multiples inside, pull-up of a deeper reflector | reversal + ≥ 2 other specific signs | ≥ 1 specific sign other than pull-up, and ≥ 3 signs in total | strong only |
| Scattered | ≥ 4 irregular blobs of its own (blobs lying on limbs of hyperbolas already found do not count), patchy fill ≤ 0.35, no continuous roof | ≥ 6 blobs + patchy | ≥ 4 blobs | 3 blobs |
| Artifact | ≥ half the zone in bottom noise; narrow (≤ 5 traces) and tall (≥ 30 % of record); on stationary traces; ≥ half inside a ringing band with no hyperbola apex | bottom noise | other three | – |

Deliberate deviation from the handoff example: "strong + discontinuous" alone is **low**, not
medium, for void-like. A red blob in an energy view is not a cavity until at least one
cavity-specific sign is present; pull-up alone is not enough either, because the limbs of a pipe or
stone crossing a deeper layer look the same. Full-line flat reflectors get a ⚠ "may be antenna ringing".

## Collapse-risk grade (`risk.py`)

From the project's collapse-risk review. Thresholds are defaults for ranking what to check
first, not calibrated probabilities, and every value used is stored in `risk.parameters`.

- Detection confidence is a separate axis; it only changes the recommended action.
- H (1–4) from cover/width: sand ≤ 0.3 / 1 / 2, clay ≤ 0.2 / 0.7 / 1.5, rock < 0.5 / 1 / 2;
  in soil, cover ≤ 0.3 m → H4 and width ≥ 1.5 m → at least H3.
- Environment factors (sand, groundwater, old pipes, rain, heavy traffic, past collapse,
  cracked/sagging surface): 0 → −1, 1–2 → 0, ≥ 3 → +1; surface sag +1 more.
- L = clamp(H + modifiers, 1, 4); score = L × C; ≥ 12 I, 8–9 II, 4–6 III, ≤ 3 IV.
- Also graded at the shallowest cover allowed by the depth uncertainty (from the velocity
  spread, default 20 %); the worse grade is used, with a ⚠ reason.
- Unknown material → sand (most conservative), unknown consequence → 3, both flagged ⚠.
  No cross line → ⚠. Rock wider than its roof is thick → ⚠ (Waltham). Soil cover > 1.5 m → ⚠
  road-cavity criteria out of scope.
- Width = the flat roof extent (without diffraction tails) when available.

## Report hook mapping

| detect | report schema |
|---|---|
| `point_target` / `unclassified` | `pipe_point` / `unknown` |
| evidence `found` / `not_assessed` | `present` / `warning` with `params.not_assessed = true` |
| risk grade I / II / III / IV | action `E` / `1` / `2` / `3` (void-like only; others left to the report rules) |
| `time_start_ns` − time zero | `time_top_ns` |
| text | Thai in `text`, English in `params.text_en` |

## Cross-line check (`crossline.py`)

One line cannot tell a stone from a pipe, or a cavity from a local lens; the neighbouring lines can.
The app runs this after all lines of a job are processed and again after a reprocess, on unreviewed
zones only. Each suspicious zone (void-like, scattered, pipe / point, unknown) gets one reason:

| Result | When | Effect |
|---|---|---|
| ✓ seen on neighbour | another line (other file, same channel) within 2 m has a suspicious zone within half the zone width + GPS error + 0.5 m, depth overlapping ± max(0.15 m, 20 %) | level kept (no upgrades until calibrated) |
| ⚠ not on neighbour | a neighbour passes over the zone's footprint (distance + GPS error ≤ ½ × half the box width; the box includes diffraction tails) and shows nothing there, and the reviewer has not excluded that part | void-like / scattered / unknown one level lower; pipe / point keeps its level and is called an isolated object (stone, debris) rather than a pipe across the lines |
| – not assessed | no GPS, GPS length differs from the wheel distance by > 15 %, GPS not RTK fixed at every fix (.cor quality 4), no line within 2 m, or neighbours too far for the zone width | none |

GPS error defaults to 0.5 m (`CrossLineParams.gps_uncertainty_m`). A line whose GPS is not RTK fixed at
every fix is left out on both sides: its own zones are not assessed, and it is not used as a neighbour
(the reason names it). Plain or float GPS can be off by metres, more than the distances judged here.

## Measuring it

- `scripts/detect_benchmark.py`: synthetic lines shaped like the CrossOver 800 MHz channel with many
  weak stones, a dipping layer, ringing, stripes and a stop; half have no target. It counts which
  medium/high zones locate a target, a stone, nothing, or something much narrower than the box.
  Before these rules: 0 of 14 targets located on 12 lines (a strong roof used up all hyperbola
  candidates and one "scattered" box covered most lines). After: 9 of 14 located, and 117 of 136
  medium/high zones sit on a real object (most of them stones, which one line cannot tell from pipes).
  `tests/test_detect_benchmark.py` keeps these from regressing. Synthetic only.
- `scripts/eval_detect_real.py "<survey folder>" [--labels <job>/labels.jsonl] --out eval_out`:
  the same processing on a real ImpulseRadar survey; zone counts per line, artifact flags, ViewPoint
  field markers with a zone nearby (markers are the operator's notes, not ground truth), the
  cross-line result, and with labels the share of proposed zones reviewers accepted, per type and
  level (`detect/evaluate.py`).

## Known limits

- Tuned on synthetic data only (tests/detect_synth.py, the processing thread's scene and the clutter
  benchmark). The real CrossOver survey was not reachable when these rules were set; run
  `scripts/eval_detect_real.py` on it and review some lines in the app before trusting the levels.
- No real accuracy figure exists yet: that needs reviewed real lines (labels.jsonl) and, for
  probabilities, dug or drilled ground truth.
- Pipe vs stone cannot be told on one line; the cross-line check helps only with good GPS and line
  spacing below ~1–2 m.
- Polarity check needs the direct wave in the input; on background-removed data it is
  `not_assessed` unless `DetectParams(surface_polarity=±1)` is given.
- Single 2-D line: width is along the line only; area and shape need cross lines.
- A cavity whose roof corners fall on full-depth stripes (as in the app demo line) loses its edge
  diffractions and is reported as scattered, not void-like.
