# Notes from reference-software manuals

What two commercial GPR programs document about their processing, and what is worth comparing
against or borrowing. **Clean-room:** everything here comes from the vendors' published user
manuals only. Nothing was taken from their program code, binaries or licensing.

| Manual | Program | Scope |
|---|---|---|
| Condor User Manual, version 2510-01 (2025) | ImpulseRadar Condor | 3D processing and interpretation of Raptor array swaths (`.ipr`, combined in ViewR first) |
| GPR-SLICE V7.MT User's Manual (updated 14 Sep 2021) | GPR-SLICE 7.x (Screening Eagle) | Full 2D/3D processing, time slices, GPS, batch runs |

Neither program chooses filters automatically, and neither marks anomalies with a confidence.
Both stay manual at the interpretation step, so those two goals of this project have no
equivalent to copy.

## Condor: processing chain and stated defaults

Condor keeps every stage as a separate data instance (raw → preprocessed → regularized →
migrated → postprocessed). The manual says the defaults "are well defined, so there's usually
no need to change settings", which makes them a useful cross-check for our data-driven values.

| Stage | Method (manual) | Values shown in the manual | Ours (`docs/processing.md`) |
|---|---|---|---|
| DC removal | Subtract the DC level per trace | Default window: lowermost 1/3 of the trace | Covered by dewow |
| Dewow | Gaussian-weighted moving average subtracted from the trace | Window / filter width in samples: 41/8 (default), 31/8, 19/3 | Gaussian, window = 2 periods |
| Bandpass | Trapezoid, frequency domain, 4 corners | Dialog: 100 / 180 / 720 / 900 MHz; worked example: 170–600 MHz | Butterworth 4, 0.4×–2× centre frequency |
| Time zero | First sample above a threshold, then the next maximum = first break; minus Tx–Rx travel time; "fixed time zero" averages over the first section | Threshold 3000, adjustment velocity 15 cm/ns | Direct-wave peak per trace, aligned to median |
| Muting | Drop scans whose mean abs amplitude is N× above / below the swath mean | High 3.0, low 3.0 (1.0 = off) | — |
| Amplitude correction | Spherical divergence (1/r), or AGC = 1 / mean abs amplitude in a sliding window | AGC 19 ns, applied at window centre | SEC gain fitted to decay; AGC 10 periods for display only |
| Predictive deconvolution | Operator from a time window, prediction gap, white noise | 7–39 ns window, gap 0.195 ns, operator 4.883 ns, white noise 1.0 | — |
| Antenna ringdown removal | Subtract the mean trace over a window of traces | 501 traces (worked example: 500) | Sliding **median** over 30 m |
| Interpolation | Split area into chunks by max gap, bilinear per time slice; optional averaging of every N slices | Max gap 0.2 m; worked example bins 4 cm | — (2D lines only) |
| Migration | 3D Stolt F-K, constant velocity; slider to test velocity on a cut-line; hyperbola fit gives RMS velocity | One parameter: velocity (cm/ns) | Stolt or Kirchhoff, off by default; velocity from x²–t² fits |
| Postprocessing | Bandpass, amplitude correction, amplitude envelope (Hilbert), thick slice | — | Hilbert envelope track |

Practical point from Appendix B: collecting denser than half the array channel spacing gains
nothing (8 cm spacing → 4 cm point distance).

## Condor: ideas worth considering

Not implemented here; listed so they can be picked up as separate issues.

- **Positioning QA before import.** Per line: GPS points/m, traces/m, start/end time, length, and a
  map coloured by points/m or traces/m. Warn when a swath outline self-intersects (sharp turns).
- **Depth-coloured thick slice (OspreyView).** One top view over a depth range, amplitude coloured
  by the depth it comes from: shallow yellow, middle green, deep blue. Fits our multi-colour view goal.
- **Processing history.** Each step stored with its parameters, viewable as a log, with branches for
  comparing settings and export/import of the whole workflow. Our `result.log` already records steps;
  branching and re-applying a saved workflow are the missing parts.
- **Velocity attached to each pick.** Every interpreted object keeps the velocity valid when it was
  picked, so exported depths stay right after the project velocity changes.
- **Interchange formats.** Appendix A documents the tab-separated object files: `.cnf` (feature
  definitions: type, group, code, colour, symbol, size, width), `.pobj` (points) and `.lobj` (lines),
  each `VERSION:` + `COORDINATE TYPE: GPS|CARTESIAN` + one `---` block per object (`FCODE<TAB>NAME`,
  then `X<TAB>Y<TAB>Z` rows; for GPS, x/y/z = lon/lat/alt). Writing these would let Condor users load our zones.

## GPR-SLICE: features relevant to this project

From the manual's contents and "What's new" section.

- **Time slices:** inverse distance and kriging gridding, multi-slice pixel maps, 2D FFT filtering of
  slices, automatic mosaic correction (zero-mean grid/line, edge and histogram matching), staggering correction.
- **Filters offered:** background removal, Kirchhoff and variable-velocity migration, 3D/2.5D migration,
  Hilbert, boxcar, regain, gain normalization, amplitude equalization, bandpass, notch, spectral
  whitening, cepstrum and spectral deconvolution, FK filtering, radargram differencing.
- **Layers and depth:** automatic horizon detection up to 15 layers, each with its own interval velocity;
  time-to-depth radargrams; metal-plate calibration for surface dielectric and layer thickness.
- **Detection:** assisted automatic hyperbola detection for bridge decks that tabulates and maps
  hyperbola peak amplitudes; anomaly picking on radargrams and 3D volumes exported as pipe features.
- **Batch ("BlueBox"):** one-button run from raw data to 3D volume with settings saved per project and
  reused; the manual stresses the user still has to choose the settings.
- **Equipment:** ImpulseRadar Raptor multichannel among many others.
- **Exports:** KMZ, world files, DXF, XYZ/XYZIRGB volumes.

Useful comparisons for us: the per-layer velocity model and metal-plate calibration (our velocity is a
single median of hyperbola fits), and hyperbola peak amplitudes as an input to rule-based scoring.
