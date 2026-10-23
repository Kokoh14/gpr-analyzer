# File formats

Every reader returns `list[Radargram]` (one per channel). `gpr_analyzer.io.read(path)` picks the reader
from the extension.

**Validation status:** ImpulseRadar is checked on a real CrossOver 4080 survey against ViewPoint's own
screenshots ([viewpoint-fit.md](viewpoint-fit.md)). The other readers are tested only against small fixture
files built from the public format descriptions; each needs one real file and a comparison with the vendor
software (trace count, samples, time window, a plotted radargram).

| Format | Extensions | Source of the layout | Notes / open points |
|---|---|---|---|
| ImpulseRadar | `.iprh` + `.iprb` (+ `.cor`) | CrossOver Data Files Formats v1.1 (public) | int16/int32 from `DATA VERSION`; trace count from file size (not `LAST TRACE`); `_0` = high, `_1` = low frequency channel; `FREQUENCY` read as sampling frequency in MHz. Checked on real files: trace spacing from `DISTANCE INTERVAL` (`USER DISTANCE INTERVAL` is only the requested value); `<line>.cor` shared by both channels; `.mrk` markers; `ZERO LEVEL` = time zero; `ANTENNA SEPARATION` used for depth. |
| MALA | `.rad` + `.rd3` / `.rd7` | Same layout as ImpulseRadar | `.rd3` int16, `.rd7` int32. |
| GSSI | `.dzt` | Public DZT header description (readgssi is GPL: reference only) | 1024-byte header per channel; 8/16-bit unsigned, 32-bit signed; `dt = range / samples`; velocity from `rhf_epsr`. Newer DZT files with GPS/extra blocks not tested. |
| Sensors & Software | `.hd` + `.dt1` | Public pulseEKKO description | 128-byte trace header (32 float32) before each trace; int16 (int32 detected from size); `dt = window / (points - 1)`. |
| SEG-Y | `.sgy` / `.segy` | SEG-Y rev 1 standard | IBM/IEEE float, int8/16/32, either byte order. **Sample interval read as picoseconds by default** (GPR convention; µs cannot hold sub-ns intervals). Pass `dt_unit="us"` otherwise. `write_segy` writes IEEE float, interval in ps. |
| SubScan (unknown) | — | Clean-room: our own files only | Use `gpr-sniff FILE --traces 555 --plot out.png`. See below. |

## Sniffing an unknown format (SubScan)

```bash
gpr-sniff LINE001.xxx --traces 555 --plot candidates.png
```

1. Lists readable text in the file (ASCII and UTF-16) with byte offsets: device name, date, frequency.
2. Tries every file-header size, per-trace header size, sample type (int8/16/32, uint8/16, float32),
   byte order and samples-per-trace that fits the file size exactly. With `--traces` (read from the
   SubScan screen) the search is much narrower.
3. Scores each layout: neighbouring traces should look alike, and each trace should be smooth in time.
   Wrong type, byte order or length gives noise or diagonal stripes.
4. Writes the best layouts as images; a person confirms which one is a real radargram.

Then `gpr_analyzer.io.sniff.to_radargram(path, candidate, dt_ns=...)` loads it. `dt_ns` comes from the
header text or from SubScan's time axis (about 145 ns over the trace in the sample screenshots). The
next step is to compare against SubScan screenshots/exports numerically and then write a proper
`read_subscan` reader.

Ambiguity to keep in mind: skipping whole traces at the start, or the first samples of every trace,
scores almost the same as the true layout. The tool prefers the smallest header whose first trace is
real data, but check the image.
