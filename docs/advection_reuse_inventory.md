# Advection research: reuse and gap inventory

Inspection date: 22 November 2026. Scope: fixed Lucas–Kanade (LK) input
parameters, statistical characterisation of observed Sydney rainfall motion,
then tracking trials against synthetic known truth. No LK parameter sweep,
optimisation infrastructure, or classifier refitting is required by this plan.
No database changes or experiment runs were made for this inventory.

## Fixed baseline

**Subsequent research decision:** use `max_corners=1000` for Bureau extraction,
with all other resolved settings unchanged. The core baseline and historical
records inventoried below retain 400. The research runner applies the explicit
override and records it in a new run manifest; 400-feature pilot outputs remain
historical provenance, not the production configuration.

The installed core implementation is the editable checkout at
`/home/alanseed/wrnz/pysteps_param`, inspected at commit
`44b7be5bbdc6c97f56c06b668fb82086dc11eb45`. The banked experiment checkout is
`../../psteps_param_wrnz_cases-classified-advection`, inspected at commit
`0150506e8c9dfd4c65eb043576675a57f6af8ddd`.

The current `dense_lucaskanade_v1` configuration is:

| Component | Fixed settings |
| --- | --- |
| Feature detection | Shi–Tomasi; max corners 400, quality level 0.025, minimum distance 5 pixels |
| LK tracking | Window 31 × 31 pixels, 3 pyramid levels, minimum eigenvalue 0.00003 |
| Outlier filtering | 3.5 standard deviations, 20 neighbours |
| Declustering | Scale 8 pixels |
| Dense interpolation | IDW, 8 neighbours, power 1.25 |
| Image opening | Resolved sparse extraction default: size 3 pixels |

Core dense configuration SHA-256:
`b157e7d52e8a5b2009b939d37d624c59c438ac8cc502db0763733cb471775e6e`.
This hashes the core method/parameter object; it is not a hash of the whole
processing chain. Preserve the resolved settings, rainfall transformation,
software versions and code revision in each new research run.

All 192 existing sparse records in `advection_experiment` have one parameter
variant. It matches the current resolved sparse configuration exactly,
including `size_opening=3`. The raw core dictionary omits that default; this
is a representation difference, not a change of LK settings.

The previous WRNZ examples use 450-second intervals. Sydney uses nominal
300-second intervals on a 1-km grid. Retain the fixed pixel settings, but
record this cadence difference: the detectable displacement and resulting
velocity sampling are not identical. Freezing LK does not freeze every
hybrid classification or interpolation option; those must have explicit,
separate experiment definitions if compared later.

## Reuse assessment

Paths below are relative to this document. `legacy/` in the table means
[the banked workflows directory](../../psteps_param_wrnz_cases-classified-advection/src/pysteps_param_wrnz_cases/workflows/).

| Capability | Existing evidence | Disposition / remaining work |
| --- | --- | --- |
| Bureau input and 1-km analysis grid | [Ingestion implementation](../src/pysteps_param_bom_cases/ingest.py), [domain contract](syd_1km_domain.md), [ingestion policies](ingestion.md) | Reuse. Read stored observations; do not build another raw-data reader. Audit final ingestion coverage before population analysis. |
| Fixed LK extraction and hybrid algorithm | Core `optical_flow.py`, `advection/_sparse_lk.py`, public `advection.ClassifiedAdvection` | Reuse current core implementation. Private sparse helpers need a deliberate supported adapter/API if used outside core; avoid maintaining another copied tracker. |
| Sparse extraction archive runner | [Legacy CLI](../../psteps_param_wrnz_cases-classified-advection/src/pysteps_param_wrnz_cases/cli/generate_sparse_advection.py) | Adapt its catalogue traversal, missing-aware reads, explicit unavailable outcomes and summary reporting. Add Sydney input identity, timing and restart contracts. |
| Sparse vector representation and persistence | [legacy/sparse_advection.py](../../psteps_param_wrnz_cases-classified-advection/src/pysteps_param_wrnz_cases/workflows/sparse_advection.py); 192 stored records | Reuse NPZ structure and coordinate/stage metadata. Store tracked vectors and outlier flags so accepted-only selection is measurable. Fixed parameter-set labels alone are insufficient run provenance. |
| Dense motion | 194 `oflow.files` records; core dense and hybrid outputs | Reuse algorithms. Save dense grids only for specified analyses/cases; sparse climatology does not require a dense grid for every pair. Verify legacy units before comparing arrays. |
| Marginals, equal-frame summaries and dense spectra | [legacy/advection_structure.py](../../psteps_param_wrnz_cases-classified-advection/src/pysteps_param_wrnz_cases/workflows/advection_structure.py), [dense analysis script](../../psteps_param_wrnz_cases-classified-advection/experiments/advection_vector_structure/scripts/analyse_dense_advection_structure.py) | Reuse equal-frame moments/histograms and regular-grid spectral utilities. Extend stratification and uncertainty aggregation to Sydney events/seasons. Dense FFT spectra describe the reconstructed field. |
| Sparse spatial and temporal structure | Sparse vectors and historical diagnostic scripts exist | New bounded analysis protocol and implementation: distance-binned component structure functions/covariances, directional dependence and temporal summaries. Account for irregular locations and rain support. A conventional FFT on scattered vectors is inappropriate. |
| Vector quality diagnostics | [legacy/tracking_reliability.py](../../psteps_param_wrnz_cases-classified-advection/src/pysteps_param_wrnz_cases/workflows/tracking_reliability.py) | Reuse forward/backward closure, patch agreement and rainfall-context diagnostics where compatible. Observational consistency is not known tracking error. |
| Synthetic rainfall and prescribed motion | [legacy/stochastic_event.py](../../psteps_param_wrnz_cases-classified-advection/src/pysteps_param_wrnz_cases/workflows/stochastic_event.py), existing generation scripts and provenance catalogues | Adapt rather than rebuild. Remove WRNZ domain/cadence assumptions; validate generated statistics independently. Sydney-calibrated parameter choices remain new work. |
| Known-truth evaluation | [Independent evaluator](../../psteps_param_wrnz_cases-classified-advection/experiments/advection_vector_structure/scripts/evaluate_experiment9_independent_validation.py), frozen manifests and member checkpoints | Reuse decision-before-truth separation, paired comparisons, hashes, checkpointing and equal-source aggregation. Configure new Sydney-based trials; do not silently redefine previous results. |
| Research storage | Existing `advection_experiment` collections and archive manifest | Reuse schema ideas in separate `BOM_advection` database. Add explicit source references, immutable run identity, result versions and publication/restart lifecycle. Keep operational `BOM` lean. |
| LK optimisation | Prior selected configuration and tuning history | Out of scope. Preserve provenance; do not port sweep runners or rerun searches. |

## Existing database and archive status

Read-only inspection of the local `advection_experiment` database found:

| Collection | Documents |
| --- | ---: |
| `sparse_oflow.files` | 192 |
| `oflow.files` | 194 |
| `synthetic_events` | 37 |
| `advection_params` | 37 |

All 37 synthetic-event documents report complete status. These counts do not
imply 37 independent validation events; development and validation inventories
have different identities and purposes.

Sparse payloads include `detected_xy`, tracked `xy`, raw `uv` and an
`outlier_mask`, with explicit pre-declustering/pre-interpolation stage metadata.
Their coordinates use image columns/rows; displacements are pixels per
timestep, with positive image-row velocity downwards. New analyses should
retain those native arrays and derive projected-grid velocities in m/s using
the grid orientation and elapsed time. Projected components must not be called
geographic east/north components without the appropriate rotation.

The legacy writer has a unique domain/product/time/member/parameter-set
identity and replaces payloads. A research run needs stronger identity and
safe publication than that overwrite contract. Store separate run, pair,
vector, optional dense-output and statistics records linked by identifiers;
small searchable metadata belongs in ordinary collections, compressed arrays
in GridFS or the manifested external archive. Avoid putting all statistics
inside opaque array payloads.

Legacy database truth-path strings were not directly resolvable at their
recorded locations. However, the relocated archive is available: all 67 files
in [the archive manifest](../../psteps_param_wrnz_cases-classified-advection/experiments/advection_vector_structure/parameters/advection_experiment_archive_v1.json)
exist at their recorded sizes under `/mnt/project/wrnz/advection_experiment`.
This includes 36 validation truth files, 9 development synthetic files and
other provenance/recovery products. This inspection checked existence and
size, not every SHA-256 or database-to-file association. Resolve references
through the archive manifest before reuse; do not interpret stale absolute
paths as lost data.

The [independent validation summary](../../psteps_param_wrnz_cases-classified-advection/experiments/advection_vector_structure/results/experiment9_independent_validation/summary.json)
reports completion on 14 November 2026, with 36 members from 12 source storms.
All 36 external member JSON checkpoints are present and report `complete`.
That supersedes earlier progress notes which could not confirm completion.
This inventory did not reproduce the evaluator, verify all hashes, or establish
that its WRNZ-based synthetic population represents Sydney.

## Required scientific contracts

1. **Sampling and missingness.** Define eligible consecutive pairs, including
   dry/zero, missing and no-vector outcomes. Read with `missing_as_nan=True`.
   Do not bridge missing slots. The stored negligible-rain policy remains
   unchanged; quantify its effect on the sampled population. Distinguish
   unavailable pairs from valid pairs with no detected/accepted vectors.
2. **Time and units.** Record nominal slot times and actual source times.
   Resolve the velocity denominator explicitly for off-grid observations;
   retain actual elapsed time and avoid silently assuming every pair is
   exactly 300 seconds. Use physical distances for statistical bins.
3. **Stages and support.** Report detected, tracked, rejected and accepted
   counts; describe mask handling and valid feature/patch support. Never
   convert missing rainfall to observed dry support. Separate raw tracked,
   filtered, declustered and interpolated outputs.
4. **Estimands.** Report total motion and translation-removed residuals
   separately. Start with component/speed marginals, vector density, rejection
   rates, spatial structure functions, directional structure and temporal
   persistence. Evaluate isotropy, component dependence and single-power-law
   assumptions before imposing them on synthetic motion.
5. **Weighting and uncertainty.** Retain per-pair summaries and event identity.
   Equal-frame and vector-weighted summaries answer different questions;
   choose the primary estimand explicitly. Use event-level blocks/replicates
   for uncertainty rather than treating correlated vectors or frames as
   independent samples.
6. **Known truth.** Freeze candidate algorithms and trial definitions before
   evaluation. Keep prescribed latent motion, estimated sparse motion and
   reconstructed dense motion separate. Use independent synthetic seeds and
   paired algorithm comparisons, with common masks and endpoint-error units.
   Record rainfall evolution and boundary effects that can make apparent
   motion differ from prescribed transport.

## Suggested implementation order

The subsequently agreed [storm-based strategy](storm_characterisation_strategy.md)
sets the population for production processing: generate and freeze the storm
catalogue after full-year ingestion, then traverse all qualifying events.
Small pilot windows below validate the implementation before that event run.

1. Freeze a small research-run manifest containing the resolved LK baseline,
   core and dependency revisions, input/grid/transform identities, pair timing,
   mask policy and selected output stages. Agree the minimal `BOM_advection`
   records and indexes using the existing sparse representation as the base.
2. Adapt a date-range runner for a few contrasting Sydney days. Record every
   pair outcome, sparse arrays and simple frame summaries. Check coordinate
   signs, m/s conversion, missing/dry/no-vector cases and reproducible restart
   before scaling to the year. Measure storage and throughput on this pilot.
3. Process the frozen storm population with the fixed tracker and generate
   non-Lagrangian rainfall-field parameters. Add statistical estimators and
   seasonal/event/rain-support summaries; retain enough raw vectors to revise
   statistical analysis without rerunning LK. Specify dense diagnostic cases
   separately to control storage and interpolation-induced interpretation.
4. Put the statistical protocol, derived synthetic specifications and paper
   outputs in the paper repository. Keep Bureau ingestion/adapters here and
   shared tracking machinery in core. Treat the banked experiment checkout as
   provenance and selectively reuse its bounded components.
5. Adapt the existing generator/evaluator to independent Sydney-based trials.
   The [draft synthetic configuration](../configs/experiments/config_synthetic_event.json)
   is a starting specification, not Sydney calibration or an executable core
   loader configuration. No LK sweep is a prerequisite for these steps.
