# Local app (upload and review)

```bash
pip install -e .[app]
gpr-analyzer-app            # opens http://127.0.0.1:8765/ in the browser
gpr-analyzer-app --workdir D:/gpr-jobs --port 8800 --no-browser
```

A small FastAPI server on the user's own computer plus one HTML/JS page (`gpr_analyzer/app/static/`).
Nothing is uploaded anywhere else. It answers the "desktop app or web app" question with both: it is
started like a program, and the screen is a web page.

Flow: drop several files (pairs such as `.iprh + .iprb`, `.rad + .rd3`, `.HD + .DT1`, plus `.txt` with the
same name) → each line is read, processed (`processing.run_pipeline`) and scanned (`detect.analyze_line`)
automatically → review screen.

Review screen:
- three views of the same line on one grid: grey (amplitude-preserving, used for scoring), high gain (AGC,
  display only), envelope colour with a dB legend and the note "red = strong reflection, not a void";
- zone boxes coloured by suspected type, hatched artifact regions, time/depth/trace/distance read-out;
- zone cards with ✓ / ✗ / ⚠ evidence, confidence (high/medium/low, never %), suggested action level;
- accept / edit (form or re-draw the box) / reject / reset, and drawing new zones the detector missed;
- the processing recipe (every step, value and reason) and a form to override velocity, band, background,
  gain, AGC window, time-zero, migration for one line, then reprocess;
- exports: report PDF / Word / HTML (draft banner until every zone is reviewed), zones CSV, survey JSON,
  processed data per line as SEG-Y or CSV, and `labels.jsonl`.

`labels.jsonl` (in the job folder) gets one record per decision: the automatic proposal, the zone before and
after, reviewer, processing options and velocity. This is the label set for later calibration / training.

Limits: jobs live in memory while the app runs (files, `survey.json` and `labels.jsonl` stay on disk);
reprocessing a line clears its reviews (they stay in the labels file).
