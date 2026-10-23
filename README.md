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

## Windows (no programming tools needed)

Download the ZIP, unpack it, double-click `START-GPR-ANALYZER.bat`. Thai guide for testing with real files and
comparing against ViewPoint / GeoLitix: [`docs/windows-testing.md`](docs/windows-testing.md).

## 3D แบบที่ 1 — positioned survey sections

The local app shows GPS-positioned radargram curtains, scan directions, a movable depth strip,
orbit/zoom, line visibility and PNG/scene JSON export. Lines without GPS can be placed using saved
local start/end coordinates. Try **ลอง 3D แบบที่ 1** on the upload page for a synthetic eight-line grid.
See [3D mode 1](docs/3d-mode-1.md) for coordinate/depth assumptions and verification.

## Development

```bash
pip install -e .[dev]
pytest
```

## Rules

- Input is ImpulseRadar (`.iprh` + `.iprb`, with `.cor` GPS and `.mrk` markers); reference software is ImpulseRadar
  ViewPoint and GeoLitix.
- **Clean-room** for reference software: we only decode our own data files and compare against what the programs
  show on screen or export. Never read or disassemble their code or touch their licensing.
- GPL projects (readgssi, RGPR) are reference only; do not copy code. GPRPy is MIT.
- No uncalibrated percentages: confidence is High / Medium / Low with reasons until ground-truth data exists.
