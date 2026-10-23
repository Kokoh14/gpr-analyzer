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
| Artifacts | `artifacts.py` | Vertical stripes: traces whose energy differs from ±25 neighbours in ≥ 50 % of 8 depth windows. Dead, clipped and stationary (operator stopped) traces. Bottom noise: below the last laterally coherent sample (dip-tolerant correlation), where the noise envelope keeps rising — i.e. gain applied to noise. |
| Contrast | `_signal.py` | Hilbert envelope ÷ background envelope at the same time (median over traces, running median over ~8 periods). Works on gained and ungained data. |
| Hyperbolas | `hyperbola.py` | Apex candidates (local maxima, moved to the earliest arrival), limbs followed outward inside the window allowed by v = 0.03–0.20 m/ns relative to the previous pick, x²–t² least-squares fit, R² ≥ 0.9 and real moveout. One-limb fits are kept (cavity edges, line ends) unless the event continues upward on the other side. Apexes on artifact traces or in bottom noise are skipped. Velocity = median of fits after outlier rejection. |
| Zones | `zones.py` | Threshold contrast ≥ 4, cut hyperbola limbs beyond the apex core, split off straight reflectors ≥ half the line long (dip up to 0.3 samples/trace), close gaps, never cross the bottom-noise onset. Narrow artifact bands (≤ 5 traces) are bridged by interpolation, wider ones masked. Bounding boxes → `Zone` with trace / time / depth / distance / width / thickness. |
| Classify | `classify.py` | Features per zone, then one rule set per type (below). Same level → the type with more of its own signs found wins. |
| Risk | `risk.py` | Void-like zones only: collapse-risk grade I–IV (see below). |

## Classification rules (no percentages)

Evidence status: `found` ✓, `absent` ✗, `warning` ⚠ (nearby artifact), `not_assessed` – (could not check).

| Type | Signs | High | Medium | Low |
|---|---|---|---|---|
| Pipe / point (`point_target`) | hyperbola apex in zone, single central apex, zone no wider than its limbs | all three | central apex | any apex |
| Layer | continuous roof (coverage ≥ 80 %, residual ≤ 1 period), wide (≥ 30 traces or 1/5 line), thin (≤ 3 periods), no two-limb apex | continuous + wide + thin | continuous + wide or thin | continuous |
| Void-like | strong, bounded; **specific:** roof polarity reversed vs the direct wave, diffractions at both edges, evenly spaced multiples inside, pull-up of a deeper reflector | reversal + ≥ 2 other specific signs | ≥ 1 specific sign and ≥ 3 signs in total | strong only |
| Scattered | ≥ 4 irregular blobs, patchy fill ≤ 0.35, no continuous roof | ≥ 6 blobs + patchy | ≥ 4 blobs | 3 blobs |
| Artifact | ≥ half the zone in bottom noise; narrow (≤ 5 traces) and tall (≥ 30 % of record); on stationary traces | bottom noise | other two | – |

Deliberate deviation from the handoff example: "strong + discontinuous" alone is **low**, not
medium, for void-like. A red blob in an energy view is not a cavity until at least one
cavity-specific sign is present. Full-line flat reflectors get a ⚠ "may be antenna ringing".

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

## Known limits

- Tuned on synthetic data only (tests/detect_synth.py and the processing thread's scene).
  Real ImpulseRadar / SubScan lines will need threshold checks.
- Polarity check needs the direct wave in the input; on background-removed data it is
  `not_assessed` unless `DetectParams(surface_polarity=±1)` is given.
- Single 2-D line: width is along the line only; area and shape need cross lines.
