# Processing workflows (#19)

In the review screen, open **Workflow** and save the current line's workflow JSON. Load a file
into A or B, compare both on the selected raw line, or explicitly apply either to that line.
A defaults to the current line when no file A was loaded. Loaded files can be reused after
selecting a different line. Comparison shows analysis images with labelled time/depth axes,
a shared amplitude scale, and separate expandable parameter histories. It does not modify
saved processing, zones or reviews. Apply uses the existing reprocessing/review-carrying path.

`processing.workflows` provides `export_workflow(result)`, `load_workflow(document)` and
`run_workflow(raw, document)`. Version 1 stores the exact ProcessingOptions, requested output
tracks, and the observed parameter table. Original None/auto choices are preserved; the rounded
history is audit context, not executable settings. This reproduces the same table and arrays
on identical raw data with the same processing implementation, while automatic settings adapt
to other input lines. Cross-version or cross-library bitwise reproducibility is not promised.
The file contains no survey payload or navigation.

Imports reject unsupported schemas, unknown options, invalid enums, nonfinite or invalid
numeric values and files above 1 MB. All ProcessingOptions fields survive app checkpoints,
including settings not exposed by the existing manual form. Older checkpoints export using
their saved line options. A failed reprocess restores the prior option selection.

Acceptance: `tests/test_workflows.py` checks exact arrays and parameter-table equality for
automatic and custom settings, reuse on a second input, history separation, invalid inputs,
API replay/compare without persistent changes, and checkpoint restore of advanced settings.
Pipeline, app and folder regressions also pass. Detector/field accuracy is outside this change.
