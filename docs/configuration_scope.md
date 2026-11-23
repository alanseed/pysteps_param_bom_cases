# Database and synthetic-event configuration

## Sydney database configuration

[config_bom_data.json](../configs/db/config_bom_data.json) contains the observed
QPE product, Sydney domain, cascade definition, operational STEPS model,
storm-selection definition and rainfall storage policy. Its nowcast model also
creates a forecast product when loaded by the core configuration loader.

The local `BOM` database was initialised and this configuration loaded on
22 November 2026. Read-back verification confirmed seven configuration documents:
two products, one STEPS model, one domain, one cascade, one storm definition
and one storage policy. The synthetic model was not loaded. Ingestion has since
completed; see the [2025 acceptance and storm record](2025_ingestion_and_storms.md).
A configuration hash and verification record are retained under
the external data root at `manifests/database_setup/BOM_configuration.json`.
The loader is append-oriented: inspect existing configuration before loading
it again.

## Synthetic-event configuration

[config_synthetic_event.json](../../psteps_advect_paper/configs/experiments/config_synthetic_event.json)
preserves the original `stochastic_event_v1` model and every generator parameter.
It retains `kind: stochastic_event` and a `models` list; the file name identifies
its experimental purpose without changing the model's existing identity.
It references the `syd_1km` domain and `standard` cascade defined by the database
configuration, rather than duplicating their definitions.

The paper repository now owns this experiment specification and its runner.
Do not pass it to `psteps-load-config`: the installed loader currently accepts
STEPS model definitions, not this generator schema. Use the paper documentation for explicit generator/configuration inputs.

## Intended scientific sequence

The agreed [storm-based characterisation strategy](../../psteps_advect_paper/docs/storm_characterisation_strategy.md)
expands this sequence: freeze the post-ingestion storm population, process all
qualifying events with fixed LK settings and non-Lagrangian field parameters,
then select representative environments for synthetic tracking trials.

1. Ingest the observed Sydney rain-rate data onto the agreed analysis grid,
   preserving missingness, actual observation times and processing provenance.
2. Evaluate the statistical structure of observed Lucas–Kanade vectors. Treat
   sparse vector observations separately from any interpolated dense field;
   decide the estimator, support masks and sampling policy before fitting.
3. Use those results to define synthetic rainfall and known motion fields,
   with recorded parameters, random seeds and generation revisions.
4. Validate tracking against the prescribed motion, using evaluation events
   and seeds independent of any classifier fitting or algorithm tuning.

The current synthetic parameters are inherited starting values, not estimates
from the Sydney archive. In particular, the existing temporal bounds cite the
previous 241-storm analysis; normal marginals, isotropy, independent components,
a single power-law spectrum and fixed motion remain assumptions to evaluate.
Keep prescribed latent motion, measured optical flow and interpolation error
separate in the eventual validation. Freeze the final experiment definitions
and selected results in the paper repository with references to these software
and configuration revisions.
