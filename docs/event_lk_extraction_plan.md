# Event-based LK extraction implementation plan

Proposed on 22 November 2026, following the accepted
[storm characterisation strategy](../../psteps_advect_paper/docs/storm_characterisation_strategy.md) and
[verified 2025 storm publication](2025_ingestion_and_storms.md).
The runner is now implemented; see [usage and verification](event_lk_usage.md).
Pilot and production runs use the research database. The full-population
production run has since completed; see the
[acceptance record](2025_lk_and_field_parameters.md).

## Scope and processing unit

Process all 107 storms in `BOM`, definition `syd_1km_qpe_standard_v1`, frozen
generation `2417c6c4be66438caac46c416efe770a`. Keep LK settings fixed to the
resolved baseline in the [reuse inventory](../../psteps_advect_paper/docs/advection_reuse_inventory.md), with
the adopted Bureau `max_corners=1000` override. All other settings are unchanged.
Extract sparse tracks before declustering or dense interpolation. Do not run
an LK parameter sweep, hybrid classifier, dense reconstruction or statistical
case selection in this first production pass.

The processing unit is a pair ending at each nominal five-minute slot from
storm start through storm end, inclusive. There are **23,134 planned pairs**.
Each pair uses the immediately preceding nominal slot, even at storm start.
That previous field is supporting input; event membership belongs to the
current slot. Never replace an unavailable predecessor with an older field.
Include permitted dry intervals inside events without another WAR eligibility
filter. Record missing and no-vector outcomes rather than discarding them.

## Input and timing contract

- Read observations from `BOM` using `missing_as_nan=True`; treat catalogue
  `data`, explicit `zero`, and `missing` states separately. Missing/uncatalogued
  inputs produce an unavailable pair, not zero velocity.
- Snapshot the storm list and selected input identities before extraction:
  filenames, catalogue status and update time, payload IDs or zero/mask
  identities, source checksums, ingestion signatures and source timestamps.
  Detect changes on resume and refuse mixed-input reuse.
- Validate domain shape, grid identity and positive elapsed source time.
  Use the configured dBR transform and 1 mm/h threshold. At that threshold
  the current transform's below-threshold value is -0.1 dBR. Record resolved
  transformation metadata explicitly and preserve NaNs.
- Store both nominal interval (300 seconds) and actual source interval. Use
  **actual elapsed source seconds** for derived physical velocities; retain
  native pair displacement so nominal-cadence comparisons remain possible.
  Flag off-grid inputs, including pairs whose offsets cancel. Missing or
  non-positive actual timing is an explicit invalid-timing outcome.
- Retain image coordinates (`x` column, `y` row) and displacements in pixels
  per pair. Derive projected-grid coordinates and m/s from verified array
  orientation and grid geometry. Positive image-row displacement is not
  automatically positive projected northing. Do not label projected
  components geographic east/north without a rotation.

## Reuse and software boundary

Reuse the core sparse extractor and resolved settings rather than copying
the banked implementation. The current implementation is in core's private
`advection/_sparse_lk.py`; expose a narrow, documented public interface before
depending on it here. Preserve current numerical behaviour, including image
preparation and outlier decisions. Verify consistency against the existing
implementation on pilot pairs.

This repository will own event traversal, Bureau input provenance, research
storage and the CLI. Proposed modules:

- `src/pysteps_param_bom_cases/event_lk.py`: pair inventory and processing.
- `src/pysteps_param_bom_cases/research_store.py`: research records and arrays.
- `scripts/extract_event_lk.py` and console command `bom-extract-event-lk`:
  thin entry point, explicit source/research databases and generation ID.
- `tests/test_event_lk.py`: pair boundaries, scientific conventions and
  failure/restart contracts.

The CLI should offer a read-only inventory mode, deterministic storm selection
for a pilot, an explicit run identity for resume, and JSON progress summaries.
It should not silently follow a newly published storm generation.

## Research storage

Use `BOM_advection`; keep source rainfall and operational storm publication in
`BOM`. Use ordinary MongoDB records for searchable metadata and GridFS for
compressed NPZ arrays. Reuse the legacy sparse array representation.

| Record | Contents |
| --- | --- |
| Run | Schema/configuration hashes, complete resolved LK settings, code/dependency versions, input snapshot, generation ID, planned counts and completion status |
| Event | Source storm ID and interval, expected/completed pair counts, outcome totals, timestamps and event completion status |
| Pair | Run/event identity, both input references, nominal/actual times, outcome/reason, counts, physical summaries and payload reference/hash |
| Sparse GridFS payload | `detected_xy`, tracked `xy`, raw `uv`, boolean LK `outlier_mask`; schema, stage and coordinate conventions |

Use unique `(run_id, storm_id, current_valid_time)` pair identity. A completed
run is immutable; changed processing settings or inputs require a new run.
Persist each payload first, then publish its pair record referencing that
payload. A crash may leave an unreferenced upload, but must not publish a
record pointing to an incomplete payload. Resume validates and reuses
completed records; it retries failed or uncommitted pairs. Report orphaned
uploads for explicit cleanup rather than deleting shared payloads.

Pair outcomes should distinguish success with accepted vectors, valid input
with no features, no successful tracks, all tracks rejected, unavailable
input, invalid timing and processing failure. Dry input is a valid input
condition, not an unavailable observation. Available zero-vector outcomes
must not be interpreted as stationary motion.

Initially store simple detected/tracked/accepted/rejected counts and component
and speed summaries for explicitly identified tracked/accepted populations.
Empty-population summaries are null, not numeric zero. Keep full vectors so
later spatial/temporal estimators do not require repeating extraction.

## Masks and quality diagnostics

Preserve both input validity masks through loading and transformation. Audit
the existing detector/tracker's treatment of masked patches and image filling
before the production run; preserving NaNs at load time alone does not prove
that a tracked patch avoids missing support.

Keep LK outlier flags separate from any additional support-quality flags.
Do not silently add a new exclusion rule to the fixed baseline. The pilot
must establish what can be inferred from the tracker about endpoints and
patch support; document any limits. If extra support diagnostics are needed,
store them as separately versioned annotations and identify their population
in later analysis. A synthetic masked-hole check is necessary even if most
Sydney observations have complete spatial coverage.

## Execution and restart

Start with one worker and sequential event processing. Keep the previous field
and its transformed image in memory when advancing one slot; load each next
field once. A bounded rolling cache avoids holding an entire long event in
memory. Reuse CRS/configuration objects and batch small database reads/writes
where useful. Checkpoint at pair level; mark events complete only after every
planned pair has a terminal scientific outcome and no unresolved failure.

Preflight validates database endpoints/access, fixed generation and configuration,
input snapshot, existing run identity, required indexes and single-writer lock.
Prepare the full pair inventory before writing results. Do not regenerate
storms or rainfall as a side effect of extraction.

## Pilot and acceptance

1. Inventory all pairs and report boundary predecessor availability, off-grid
   source times, mask fractions and zero-field counts before extraction.
2. Choose a small deterministic set of contrasting events from duration, WAR
   and season, without selecting on tracking success. Include boundary and
   off-grid pairs explicitly if those do not occur in the selected events.
3. Verify known translated fields for displacement direction and m/s conversion,
   plus dry fields, absent predecessors, mask holes and no-vector outcomes.
   Confirm raw arrays and outlier decisions match the current fixed extractor.
4. Interrupt/resume the pilot and confirm identical scientific results, no
   duplicate pair records and readable referenced payloads. Measure runtime,
   peak memory and bytes per pair to estimate full-run cost.
5. Run all 107 events. Acceptance requires all 23,134 planned pairs accounted
   for, no unresolved processing failures, valid payload references, consistent
   hashes and units, count identities (`tracked = accepted + rejected`) and
   aggregate totals matching event/run summaries. Unavailable pairs remain
   explicitly reported and do not prevent an otherwise complete inventory.

No full-year extraction is started by acceptance of this planning document.
Implementation and pilot verification come first.

## Non-Lagrangian parameters and later analysis

Plan non-Lagrangian rainfall parameters as a sibling stage keyed by current
field and estimator configuration, with links to the same event/run inputs.
They should not depend on successful tracking, and their failures should not
invalidate reusable LK outputs. Fix the core parameter reader's missing-data
behaviour and specify fit/threshold metadata before enabling this stage.
The field cache can be shared if stages are subsequently executed together.

After extraction and field-parameter acceptance, build event descriptors and
the sparse structure analyses in the agreed strategy. Final terciles,
representative selections and synthetic truth experiments remain separate
scientific decisions.
