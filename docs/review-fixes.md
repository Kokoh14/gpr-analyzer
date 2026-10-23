# Survey correctness and performance fixes

Base: `95b461e` (includes PRs #13 and #14). This change applies the actionable correctness and performance findings from the 2026-10-23 whole-program review. It preserves the offline viewer, analysis/display separation, public default pipeline outputs, saved review records and existing export formats.

## Findings and acceptance

| ID | Problem and resulting behavior | Verification |
|---|---|---|
| F1 | A closed 3D card retained old data after reprocessing. Every published line now has a processing revision; a changed revision invalidates the scene even while closed and reopening fetches fresh data. Stale in-flight responses are discarded. | Chrome: changed velocity and reprocessing again with identical options both refresh on reopening. |
| F2 | Core/3D used antenna separation while detector, report, 2D/map and comparison used `vt/2`. Zone/line records now carry separation with a backward-compatible zero default. Detection depth/thickness, uncertainty bounds, figures, reference matching, map bands/readouts and dielectric-point display use the shared conversion and its inverse. | Python cross-path/round-trip tests at zero/nonzero separation; Chrome nonlinear geometry bound. |
| F3 | Shared dual-channel GPS and marker files were absent from checkpoint fingerprints. Fingerprints now include names, nanosecond mtimes and sizes of channel/shared dependencies. A dedicated processing revision invalidates older semantic results. Pending options/reviews/ground truth persist across save/restart/resume during refresh. | Synthetic sidecar additions and obsolete-checkpoint recovery, including reviewed/manual zones and confirmed ground truth. |
| F4 | Scale/sidebar refresh discarded unsaved coordinates. Per-line drafts survive display and track/frame refreshes until save or a job switch. | Chrome draft retained through scale and frame changes. |
| F5 | Rendering/picking used the full image grid. Adaptive geometry bounds depth interpolation error to 1–5 mm on the existing display grid; a separate boundary mesh handles picking, with depth/time recovered from the original nonlinear grid. Hover is limited to one requestAnimationFrame and paused while dragging. | Chrome visual/selection tests, nonlinear sample-error checks and 40-line synthetic benchmark. |
| F6 | Every scene request copied/scanned all arrays and rebuilt all PNGs under the job lock. Contrast and texture caches are tied to result identity; percentile scratch storage does not mutate source arrays. Rendering uses a snapshot captured under a short lock. | Cache reuse/invalidation and source immutability tests; texture memory/time benchmark. |
| F7 | Unused layers/energy and CLI display tracks were computed eagerly. The app requests its four used tracks; the CLI analysis hook requests analysis only. Existing `run_pipeline(..., tracks=None)` callers still receive every track. Logs list only work actually performed. | Exact analysis/envelope equality; unrequested feature functions fail if called in the selective-path test. |
| F8 | Detector envelope/direct-wave/period work was repeated on the same analysis grid. Contrast, bottom-noise and polarity share these features within a detection call. | Exact detection JSON equivalence with supplied float64 features; profiler reduces Hilbert calls from 7 to 5. |
| F9 | Selecting a short interval still read the full primary payload. ImpulseRadar/MALA use header-derived trace bounds and bounded `fromfile(count, offset)` reads. Shared crop metadata preserves GPS anchors, markers, dead traces and original trace offset. Companion previews read only the requested byte limit. | Int16/int32 range-vs-full-crop equality and actual read-count/offset assertions; preview cannot call `read_bytes`. |
| F10 | All opened jobs stayed strongly held and comparison was repeatedly matched. An LRU retains three recent jobs; a weak identity index reuses objects still owned by active requests/workers. Disk checkpoints remain intact. Per-line comparison caches include picks/zones/geometry/tolerance in their keys. | Active-object identity, idle-object collection, disk reopen and comparison invalidation tests. |
| F11 | Each report format rerendered unchanged figures. A bounded per-view figure cache survives new survey wrappers for the same line result and invalidates on annotations/reviews/render options. Captions and report text are still composed for the requested language. | Same figure reused; rejecting a zone rerenders and changes image bytes. |
| F12 | Every slice input disposed/rebuilt all geometry. Strip topology/buffers are reused and only depth/UV values change, at most once per frame. | Chrome depth-strip workflow and slice benchmark. |

## Compatibility and remaining limits

- No survey files, labels or on-disk jobs are deleted. Old checkpoints require a one-time refresh via Resume; pending review state remains saved through interrupted refreshes. Automatic reviewed zones use the existing overlap-matching policy when regenerated, and manual zones retain their time/trace bounds with the current line velocity/reference.
- The existing core direct-ground-wave depth convention is reused; this establishes internal consistency, not new field calibration. The zero-offset hyperbola fitting and migration models are unchanged. Instrument/time-zero/velocity validation with real data remains necessary.
- ImpulseRadar/MALA gain ranged I/O. Other supported readers still use their established full-read path before cropping; format-specific ranged indexing remains future work.
- Inline PNG scene JSON is retained for compatibility; binary texture endpoints/BVH are unnecessary for this patch. Initial texture contrast still scans the source once per track. No approximation of percentile contrast was introduced.
- The cache limit bounds idle retained jobs, not all active requests or the size of a single survey. Full job-state saves remain synchronous for review durability; matching/figure work is cached rather than delaying persistence.
- The pipeline's public default still includes layers/energy. They are optional for selected workflows rather than removed from the API. CLI hook cleanup, normalization unification and changes to scientific detection/classification rules are deferred because they require separate compatibility/behavior decisions.

## Verification

- Full Python suite on Windows: **235 passed, 6 skipped** (106.23 s). Report cache checks after making caching opt-in for immutable inputs: **42 passed, 1 skipped**. Six suite skips require optional real-data/pypdf inputs; a Starlette/httpx deprecation warning is upstream.
- `scripts/verify_scene3d.cjs`: existing orbit/visibility/depth/pick/export/layout-reload checks plus draft, closed-card/same-options refresh and nonlinear-mesh checks. It runs against a disposable test server and instruments only its own browser response.
- `scripts/benchmark_review.py`: synthetic-only retained arrays, Hilbert count and cold/cached texture costs. Optional positional argument writes JSON evidence. The CLI enables figure sharing only inside its immutable multi-format export batch; generic report API callers do not opt into caching by default.

Observed synthetic benchmark on the same Windows host (Chrome/SwiftShader, 1600×1100, vertical scale 2):

| Metric | Review baseline | After change |
|---|---:|---:|
| 40-line raycast median | 46.9 ms | 2.2 ms |
| 40-line scene build, one run | 187.9 ms | 60.7 ms |
| 40-line slice update median | 5.2 ms | 0.7 ms |
| Rendered triangles including passes | 4,083,600 | 960,400 |
| Retained processing tracks per sample line | 7.68 MB | 5.50 MB (app) |
| Hilbert calls, processing + detection | 7 | 5 |
| 22 MB source → 160×192 grey texture, cold median after import warm-up | 62.6 ms | 43.3 ms |
| Same texture, cache hit median | no cache | 0.016 ms |
| Texture temporary allocation peak seen by tracemalloc | 66.3 MB | 49.7 MB |

These are diagnostic synthetic measurements, not hardware-independent speed guarantees or field accuracy results. Scene build timing is a single run, raycast/slice use 12 calls, texture medians use three calls. Tracemalloc and retained-array totals are not whole-process peak RSS.

Local evidence: `tmp/review-pr-ui/verification.json`, `tmp/review-pr-ui/backend-benchmark.json`, screenshots in that directory (not included in the source package).
