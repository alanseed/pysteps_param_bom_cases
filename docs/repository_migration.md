# Scientific experiments moved to the paper repository

The November 2026 statistical analyses and synthetic experiments are now owned
by [psteps_advect_paper](../../psteps_advect_paper/README.md), in package
`psteps_advect`. See the paper's
[migration record](../../psteps_advect_paper/docs/repository_migration.md) and
[accepted plan](../../psteps_advect_paper/docs/experiment_repository_migration_plan.md).

This repository retains NCI acquisition, Bureau remapping/ingestion, configured
storm products, event-based LK extraction, field-parameter archive processing,
storage access and the pilot-track QA figures. Its three installed archive
commands retain their existing names and contracts. The paper consumes those
products through an explicit dependency; cases has no dependency on the paper.

Existing database documents, source rainfields and completed run archives are
unchanged. Their execution hashes refer to their original frozen sources, not
the renamed package. Do not bypass source-identity checks to resume an old run.
An exact pre-migration recovery copy, environment records and source-to-target
hash map are retained at
`~/data/alan/psteps_advect_data/runs/repository_migration/20261124_cases_to_paper/`.
No commits or pushes were performed as part of this move.
