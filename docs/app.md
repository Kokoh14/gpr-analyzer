# Local app (upload and review)

```bash
pip install -e .[app]
gpr-analyzer-app            # opens http://127.0.0.1:8765/ in the browser
gpr-analyzer-app --workdir D:/gpr-jobs --port 8800 --no-browser
```

A small FastAPI server on the user's own computer plus one HTML/JS page (`gpr_analyzer/app/static/`).
Nothing is uploaded anywhere else. It answers the "desktop app or web app" question with both: it is
started like a program, and the screen is a web page.

Flow: choose the data folder (system folder dialog, or the in-page browser `/api/fs`) → completeness check
(`app/folders.py`, headers and side files only: pairs present, whole traces, LAST TRACE, GPS coverage, gaps and
length, markers) with a rough map of the tracks → tick lines and optionally a distance range per line → the job
reads the files in place (nothing is copied). Dropping files still works (they are copied into `uploads/`).

Processing runs in a background thread, one line after another. The page polls `/api/jobs/<id>/progress` and
shows each file's stage (read, each processing step, detection, checkpoint); `log.txt` in the job folder gets a
line per stage and a summary per line (zones by type, suspicious void-like zones with position and depth).
Each finished line is pickled to `checkpoints/<line>.pkl` (raw, display tracks, automatic zones), so reopening
a job needs no reprocessing, and a job cut off by a crash or power failure shows as interrupted and continues
with the unfinished files (`/resume`). A checkpoint is redone if its source file changed. With a distance range
only those traces are processed; trace numbers on screen count from the start of the range, distances in
metres (axis, zones, report, reference matching) from the start of the line.

Older flow, still supported: drop several files (pairs such as `.iprh + .iprb`, `.rad + .rd3`, `.HD + .DT1`, plus `.txt` with the
same name) → each line is read, processed (`processing.run_pipeline`) and scanned (`detect.analyze_line`)
automatically → review screen.

Review screen:
- views of the same line on one grid: grey (amplitude-preserving, used for scoring), high gain (AGC,
  display only), envelope colour with a dB legend and the note "red = strong reflection, not a void", and the
  ViewPoint-style screen (`processing/viewpoint.py`, off by default, tick it to compare with the unit's software);
- zoom shared by every view: mouse wheel (Shift = distance axis only), drag to pan, drag a zoom box, double-click
  for the whole line; the axes follow the zoom;
- zone boxes coloured by suspected type, hatched artifact regions, time/depth/trace/distance read-out;
- zone cards with ✓ / ✗ / ⚠ evidence, confidence (high/medium/low, never %), suggested action level;
- accept / edit (form or re-draw the box) / reject / reset, and drawing new zones the detector missed;
- the processing recipe (every step, value and reason) and a form to override velocity, band, background,
  gain, AGC window, time-zero, migration for one line, then reprocess;
- exports: report PDF / Word / HTML (draft banner until every zone is reviewed), zones CSV, survey JSON,
  processed data per line as SEG-Y or CSV, and `labels.jsonl`.

`labels.jsonl` (in the job folder) gets one record per decision: the automatic proposal, the zone before and
after, reviewer, processing options and velocity. This is the label set for later calibration / training.

Jobs are saved to `<workdir>/<job>/state.json` after every change (info, per-line options, zones and reviews,
comparison data). The start page lists earlier jobs; opening one re-reads its uploads, reprocesses with the
saved options and puts the zones and reviews back. Reprocessing a line clears its reviews (they stay in the
labels file).

## Before the report

- **Sections not used**: drag on a radargram (✂) to mark a distance range whose results are not used (bad data,
  parked car, outside the survey area) with a reason. Zones whose centre falls inside are left out of the report,
  and the range is hatched in the figures as "section excluded by the reviewer". Undo by removing the section.
- **Line status**: pending / approved (use in the report) / needs reprocessing, with a note.
- **Reprocessing keeps the review**: zones drawn by the reviewer stay, and an accept / reject / edit on an old
  automatic zone moves to the new zone covering the same place (box overlap ≥ 0.5). Unmatched decisions are
  listed in the `reprocess` label record.
- **Summary before the report** (`/api/jobs/<id>/summary`): what is still open (unreviewed zones, lines not
  approved, default velocity, no GPS, missing job info, unfinished processing), job info, the zones that need
  action (levels E / 1 / 2) with distance, depth and coordinates, and a table per line; report buttons on the
  same page. All of these decisions go to `labels.jsonl`.

## Map (top view)

`gpr_analyzer/app/geo.py`, laid out after IDS AiMaps. When a line has a `.cor` GPS file, every trace gets a
position (interpolated between fixes) and the map shows:

- every line of the job (click one to open it), start/end points and `.mrk` markers;
- the open line coloured by a top-view energy slice: for the chosen depth band, the strongest Hilbert envelope
  per trace in dB below the line's maximum, with a hide-below threshold and Jet / Hot / grey colours. This is
  relative energy for finding things on the map, not a probability; confidence stays high/medium/low;
- zones along the track (dashed until reviewed), clickable to select them.

Clicking the map moves the radargrams to that trace; clicking a radargram picks the slice depth and shows the
point on the map. Zones in the report and CSV get WGS84 start/end coordinates from the same track.

Basemaps are online tiles: Esri World Imagery (satellite) and OpenStreetMap, with their attribution on the map.
Without internet the tracks, slice and scale bar still show on a blank background. Leaflet 1.9.4 (BSD-2) is
bundled in `static/vendor/leaflet/` so the page needs no CDN.

## Comparing with reference software (ViewPoint, GeoLitix)

`gpr_analyzer/app/compare.py`. The licensed programs are black-box references: we only use what the user
exports or reads off their screens. Per line the user can

- attach reference images (screenshots or exported pictures) shown beside our views;
- import reference picks from a CSV/TXT export. Columns are found by name in English or Thai, with units in
  brackets (`Distance [m]`, `Depth (cm)`, `time_ns`); a row is a box (start/end, top/bottom) or a point.
  `,` `;` tab and `|` separators, decimal commas and cp874 (Thai Windows) files are accepted. A template is at
  `/api/compare/template.csv`;
- add picks by typing numbers, or by drawing a box on our radargram where the reference program marked
  something (stored in traces / ns, so no velocity conversion);
- type the line facts the reference shows (trace count, time window, length, velocity, max depth). Mismatches
  point at reader bugs; the velocity is also used to convert that program's depths before matching.

Matching: a pick pairs with at most one of our zones that is not rejected and not an artifact, when the gaps in
distance and depth are within the tolerance (default 1.0 m and 0.3 m, editable). Each row gets an automatic
status (match / we missed it / we found extra) and the reviewer can override it (match, partial, missed,
unsure; for our extras: probably real, false alarm), force a pairing and add a note. Recall = (match +
partial) / reference picks; precision counts extras not yet judged as false alarms.

Exports: `comparison.csv` (row per pick or extra zone), `comparison_lines.csv`, `features.csv` per job, and
for every saved job at once `/api/compare/export/comparison_all.csv`, `comparison_lines_all.csv`,
`features_all.csv`. All CSVs have a BOM so Excel shows Thai. Comparison actions are logged to `labels.jsonl`
too (`reference_pick`, `reference_import`, `compare_verdict`).

## Windows

`START-GPR-ANALYZER.bat` (double-click) runs `windows/launch.ps1`: finds Python 3.10+ (installs 3.12 per
user with winget if missing), creates `%LOCALAPPDATA%\GPR-Analyzer\venv`, installs `.[app]` there, reinstalls
only when the program files change, adds a desktop shortcut, and starts the app with jobs in
`Documents\GPR-Analyzer-jobs`. `-SmokeTest` is used by `.github/workflows/windows.yml`. User guide in Thai:
`docs/windows-testing.md`.
