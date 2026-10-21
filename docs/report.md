# Report (`gpr_analyzer/report`)

One report per survey, written from one set of records:

```bash
gpr-analyzer analyze LINE001.iprb LINE002.iprb --surface road --lang bi \
    --report out.pdf --report out.docx --report zones.csv --report survey.json
gpr-analyzer analyze --demo --report demo.pdf      # mock data, no reader needed
```

`--lang`: `th`, `en` or `bi` (Thai first, English second). Formats come from the extension:
`.pdf .docx .html .json .csv`.

## Zone schema (what `detect/` should emit)

`gpr_analyzer.report.schema.Zone`, or a dict with the same keys (`Zone.from_dict`). JSON via
`SurveyResult.to_json()`; `schema_version` = 0.1.

| Field | Meaning |
|---|---|
| `zone_id`, `line_id` | e.g. `A`, `L1` |
| `trace_start`, `trace_end` | inclusive trace range |
| `time_top_ns`, `time_bottom_ns` | two-way time range; depth is derived as v·t/2, always shown as a range |
| `velocity_m_per_ns`, `velocity_source` | `hyperbola_fit` / `default` / `user` / `cmp` / `file_header` |
| `velocity_uncertainty_m_per_ns` | optional, widens the printed depth range |
| `suspected_type` | `void_like`, `pipe_point`, `layer`, `scattered`, `artifact`, `unknown` |
| `confidence` | `high` / `medium` / `low`; `confirmed` only with a `ground_truth` record |
| `action` | `E` / `1` / `2` / `3` / `none`, or `None` to use the provisional matrix in `rules.suggest_action` |
| `distance_start_m`, `distance_end_m`, `coord_start`, `coord_end`, `datum` | the other two location references |
| `evidence` | list of `Evidence(kind, code, text, params)`; kind = `present` ✓ / `absent` ✗ / `warning` ⚠; codes translated in `i18n.py` (`ev_<code>`), unknown codes print `text` |
| `artifact_flags` | short strings shown in the zone table |
| `review` | `auto` (machine-proposed) / `accepted` / `edited` / `rejected`; any `auto` zone makes the report a watermarked draft |
| `ground_truth` | dig/drill result, for later calibration |

`detect_zones(radargram, line_id=...)` may also return `(zones, artifact_regions)` where each
region is `ArtifactRegion(trace_start, trace_end, reason)` (hatched on the figures).
Processing may return `(radargram, steps)` with steps as `ProcessingStep(name, params, auto)`,
`(name, params)` tuples or dicts; `name` codes are translated as `step_<name>`.

## Writing rules (enforced by `rules.find_wording_violations` in tests)

- Never "void found" / "พบโพรง" without ground truth: say void-like zone / โซนคล้ายโพรง.
- No percentages anywhere: confidence is a rule-based grade with reasons.
- Every colour figure caption says red = high amplitude, not a void.
- Every recommendation names its zone ids.

## Fonts

PDF embeds Sarabun (SIL OFL, `report/fonts/OFL.txt`) with HarfBuzz shaping so Thai marks sit
correctly; DejaVu Sans (from matplotlib) is the fallback for ✓ ✗ ⚠. Word uses Calibri for Latin and
Leelawadee UI for Thai (`render_docx(..., thai_font="TH Sarabun New")` to change).
