# Condor object export (#20)

Use **Condor ZIP** beside CSV/JSON to download `zones.cnf`, `zones.pobj`, `zones.lobj` and a
manifest. `write_report(survey, path)` also accepts the three object extensions individually;
download the matching CNF definitions together with the object files. Python callers can use
`report.exports.zones_condor(survey, coordinate_type=None)` or `condor_zip(survey, path)`.

The writer follows Appendix A: UTF-8, CNF version 1 with seven tab-separated fields, and object
version 2 with a coordinate-type header and delimited feature/XYZ blocks. Definitions use
interpretation group 2, one class/confidence code per geometry type. Each zone produces a centre
point and, when it has distinct endpoints, a line along its extent. Confidence is a rule-based
grade; object names retain review state. Rejected zones are omitted.

GPS X/Y are longitude/latitude. Z is recorded surface altitude minus the midpoint of that zone's
top/bottom depths, calculated with its own stored velocity and antenna separation. The app carries
surface elevations from bracketing COR rows; it does not extrapolate heights or invent them.
The source's vertical datum is not verified. Missing GPS heights/positions appear in the manifest's
skipped list; if no zone is eligible, export returns an explanatory error.

Declared CARTESIAN coordinates are passed through without transformation, in a single coordinate
system. Their Z is negative depth below relative surface zero, explicitly recorded in the manifest.
Mixed GPS/projected frames require explicit selection in the Python API. Undeclared systems are
skipped. GPS and CARTESIAN are never silently mixed in one object file.

Acceptance: `tests/test_condor_exports.py` compares exact CNF/POBJ/LOBJ text, checks UTF-8 and ZIP
contents, validates different per-zone velocities/nonzero separation, relative Cartesian depth,
missing altitude, skipped/rejected records and the app endpoints. Report/schema/app regressions
also pass. **Importing the result in a licensed Condor installation remains a manual check.**

Sources: issue #20 and `docs/reference-manuals.md` summarize manual 2510-01. The same Appendix A
layout was checked against the manufacturer's current
[Condor User Manual 2026-01, pp. 59–61](https://impulseradargpr.com/wp-content/uploads/2026/01/Condor-User-Manual-ver.-1.00.pdf)
using its extracted text. No proprietary software code or binary was used.
