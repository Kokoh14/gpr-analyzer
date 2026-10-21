# gpr-analyzer

Automatic GPR (ground-penetrating radar) analysis: read raw files, process without manual filter tuning,
show several colour views, mark anomalies with a rule-based confidence and reasons, flag artifacts,
and produce a report.

## Layout

| Package | Purpose |
|---|---|
| `gpr_analyzer/core.py` | `Radargram` data model shared by everything (data shape = samples × traces) |
| `gpr_analyzer/io/` | File readers (ImpulseRadar, GSSI DZT, MALA, Sensors & Software, SEG-Y) + format sniffer |
| `gpr_analyzer/processing/` | Dewow, time-zero, bandpass, background removal, gain, migration, attributes |
| `gpr_analyzer/detect/` | Anomaly detection, artifact flags, rule-based scoring |
| `gpr_analyzer/report/` | PDF / Word / HTML report, CSV / JSON / GeoJSON zone exports (see `docs/report.md`) |
| `gpr_analyzer/cli.py` | `gpr-analyzer analyze <files> --report out.pdf` |
| `gpr_analyzer/synthetic/` | Synthetic radargrams for tests |

## Development

```bash
pip install -e .[dev]
pytest
```

## Rules

- **Clean-room** for SubScan: we only decode our own data files and compare against what SubScan shows on screen.
  Never read or disassemble SubScan code or touch its licensing.
- GPL projects (readgssi, RGPR) are reference only; do not copy code. GPRPy is MIT.
- No uncalibrated percentages: confidence is High / Medium / Low with reasons until ground-truth data exists.
