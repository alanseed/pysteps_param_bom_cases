# Native archive products for the 2025 Sydney storms

This stage populates the local `BOM` database with dense apparent-motion fields,
seven-level rainfall cascades and native forecast parameter documents for the
same **107 storms / 23,134 current fields** used in E1. All timestamps are UTC.
It prepares archive-mode processing; it does not run a nowcast or validate a
new motion estimator. Scientific comparisons remain in the paper repository.

## Fixed baseline and reuse

The runner consumes completed `BOM_advection` runs:

- `syd_2025_fixed_lk_v1`: accepted sparse observations and actual source intervals;
- `syd_2025_fields_v1`: missing-aware rainfall marginal and spectral estimates;
- `syd_2025_characterisation_v1`: searchable feature-support and motion summaries.

It does not rerun the LK parameter optimisation or change any research result.
Accepted vectors undergo the native 8-pixel declustering and IDW interpolation
(`k=8`, power 1.25). Velocity is stored in m/s using each pair's actual source
interval. The dense grid describes apparent rainfall-pattern motion, including
growth and decay effects, rather than wind velocity.

The core registers **`dense_lucaskanade_1000_v1`**, a separately hashed method
with the study's maximum of 1,000 detected features. This leaves the original
400-feature `dense_lucaskanade_v1` default unchanged. The BOM input configuration
selects the new variant so native consumers validate the correct method/hash.
This small core addition must accompany deployment of this cases runner.

## Stored products and metadata

- `oflow.files` and `oflow.chunks`: native schema-v4 packed physical motion;
  0.01 m/s resolution, nearest rounding, maximum quantisation error 0.005 m/s.
- `oflow_catalogue`: authoritative `data` or `missing` result per input identity.
  Available GridFS and catalogue metadata agree. Missing results have a reason
  and no numerical payload. Neither no accepted features nor missing input is
  silently treated as zero motion; genuine zero vectors remain valid zero motion.
- `cascade.files` and `cascade.chunks`: standard seven-level normalized dBR
  decomposition, including means/SDs needed for recomposition.
- `params`: schema-v5 estimation documents with the existing rainfall marginals,
  spectra and native Lagrangian lag-1/lag-2 correlations and correlation time.
  Original estimates are retained; no forecast-time clipping/fallback is applied.
- `archive_runs`, `archive_events`, `archive_products`: manifest, completion and
  restart checkpoints. These are bookkeeping, not new rain observations.

The existing native optical-flow schema already records packing, units, method,
configuration hash and **dense-grid** velocity/speed statistics. It does not need
a breaking schema change. Additional metadata records:

- `archive`: run, originating storm/generation, current source identity/hash,
  source payload ID, support-only flag and missing-data policy;
- `research_reference`: database, sparse run/pair ID and payload checksum,
  field-parameter run and E1 characterisation run;
- actual source times/interval, sparse counts and original support diagnostics;
- projected-coordinate convention (array rows increase projected y).

These references permit joins to E1 rather than duplicating its full feature
analysis. Sparse-feature statistics and dense-grid statistics have different
populations and must not be substituted for each other.

## Cascades, temporal support and missingness

Two preceding five-minute fields are added to each event for the three-field
AR(2) history: **214 supporting slots**, some shared with preceding events.
They receive cascades where possible, but do not extend the frozen event
population or receive additional event parameter/motion records. Shared cascade
identities are reused; events sharing support execute sequentially within a
batch, while independent batches may run in parallel.

Input rainfall is decoded with `missing_as_nan=True`. Cascades require a fully
observed grid; missing pixels are not infilled as dry rainfall. An unavailable
supporting cascade makes the corresponding temporal estimate explicitly
unavailable. Native `lagr_auto_cor` fills pixels advected outside the domain
with zero in **normalized cascade space**. This existing boundary convention is
recorded separately from the treatment of missing input observations.

Temporal diagnostics use the **packed then decoded** current dense velocity,
matching forecast consumers, with nominal five-minute lags. The actual source
interval has already been used to convert LK displacement into physical velocity.
The current velocity is held fixed while bringing both earlier cascades forward,
as in the native estimator. These are apparent Lagrangian rainfall-evolution
statistics: rainfall evolution, motion error and boundary effects contribute.
They do not independently calibrate a latent evolution timescale or latent
advection beta. Undefined estimates remain null with temporal status/reason. Temporal status
`available` means all lag correlations are finite; an individual correlation-time
estimate can still be undefined under the native conversion and remain null.
The runner does not replace such values by climatology.

## Running and resuming

Use the `bom_cases` environment and editable native core installation:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/prepare_event_archive.py --dry-run
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/prepare_event_archive.py \
  --storm-id 6b02748b146d6af5e47fae42 --workers 1
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/verify_event_archive.py --partial
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/prepare_event_archive.py --workers 12
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/verify_event_archive.py
```

`--storm-id` restricts execution, not the full frozen manifest. The July pilot
therefore contributes to the same final run. Repeating the command reuses
completed per-field checkpoints after checking source identities and loads
supporting cascade histories. A missing/unreadable completed cascade is an
error. Run the verifier after resumption to check native payload references and
all parameter joins. The exclusive local lock prevents competing runner instances.
Unrelated archive products are never overwritten. Failed writes before a
checkpoint can be repeated; completed provenance/configuration changes require
a new run identity and explicit product migration rather than silent reuse.

Manifests, input snapshots, copied numerical source, summaries and verification
reports live outside Git under
`~/data/alan/psteps_advect_data/runs/archive_events/syd_2025_archive_v2/`.
The initial development pilot `syd_2025_archive_v1` stopped because a projected
source record omitted descriptive metadata. It published no native payloads;
its two unavailable-support checkpoints and failed-pilot status are retained.
The corrected frozen run is v2.

## Verification

The read-back verifier checks complete population/unique identities, all
parameter marginal values against the frozen research records, catalogue/GridFS
metadata agreement, missing-motion semantics and checkpoint cascade references.
For three positions per event it checks cascade reconstruction and native
parameter-consumer correlation calculations. Up to six fresh native 1,000-feature
LK extractions independently check the reused-sparse dense grids to packing
tolerance. This tests physical conversion, interpolation and consumer compatibility.

The cases suite passed **46 tests**, including new checks for constant/zero
interpolation, empty/nonfinite sparse support and missing/perfectly persistent
temporal histories. The focused native method/optical-flow suite passed **6
additional core tests** (11 focused cases/core tests in total). Final database
acceptance counts are recorded below after verification.

### July pilot acceptance

The complete July event (30 June 03:55–2 July 23:20 UTC) produced 810
cascades and parameter records, 809 available motion fields, one missing-motion
record and 808 complete temporal estimates. Its two preceding support slots
were unavailable, explaining the first two unavailable temporal estimates.
The pilot completed in 318 seconds with one worker.

Independent read-back checked all 810 marginal/source joins. Three cascade
reconstructions agreed within 1.07e-14 dBR. Two fresh native LK calculations
agreed within 0.005 m/s, and two native temporal calculations agreed exactly.
The frozen-manifest full-population pass then resumed these products with 12
workers. Full acceptance is recorded below.


### Full-population acceptance: 25 November 2026

Run `syd_2025_archive_v2` completed all **107 events**. The 12-worker full pass,
including reuse of July, took 1,164.5 seconds (19.4 minutes). The independent
read-back verifier exited successfully.

| Product or outcome | Count |
| --- | ---: |
| Current event fields / native parameter records | 23,134 |
| Available physical dense motion fields | 23,112 |
| Explicitly missing motion records, no numerical payload | 22 |
| Unique available cascades, including supporting fields | 23,312 |
| Event/support checkpoint slots | 23,348 |
| Unavailable supporting cascade slots | 28 |
| Parameter records with both lag arrays finite | 23,090 |
| Parameter records with unavailable temporal support | 44 |
| Defined correlation-time estimates | 22,802 |

The checkpoint cascade-data count is 23,320 because eight available timestamps
serve both an event and the next event's supporting history. Their payloads are
shared, giving 23,312 unique cascade files. No duplicate native motion or
parameter identities were found. The 22 missing motion outcomes match the
original sparse run's unavailable predecessors. Temporal unavailability remains
explicit; a further 288 finite-lag records have an undefined native correlation
time. These are null estimates, not substituted zero or climatological values.

All parameter marginals and source references were reconciled against the
completed research runs. Available optical-flow catalogue and GridFS metadata
agree; missing motion has no payload. All 23,348 checkpoint cascade references
were checked. Sampled numerical checks across the 107 events passed:

- **321** cascade reconstructions: maximum absolute error **1.43e-14 dBR**;
- **6** fresh native 1,000-feature LK extractions: maximum dense-motion difference
  **0.005 m/s**, consistent with nearest-rounding storage precision;
- **299** native temporal-consumer checks: maximum lag-correlation difference
  **0.0**; stored defined correlation times also agree.

Read-back also confirmed that a native `StepsParameters` object loads the pilot
records. An observed-dry, completely observed field produces finite native
cascades. Source rainfall and the completed research collections were not
rewritten. The BOM analysis configuration now explicitly selects the registered
1,000-feature baseline; its previous 400-feature native default remains intact.

The archived `verification.json` records these checks, the frozen manifest
identity and verifier-source hash. `verify_source.py` preserves the verifier.
Configuration, runner, tests and documentation are local changes; the small
native method registration and its test are in the editable core checkout.
