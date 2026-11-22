# 2025 ingestion acceptance and published storm population

Reviewed and generated on 22 November 2026, following the accepted
[storm characterisation strategy](storm_characterisation_strategy.md).

## Ingestion review

The completed cached ingestion continuation exited successfully with zero
failures. The review reconciled all 365 authoritative daily reports with the
local `BOM` field catalogue: the original run through 17 March, followed by the
cached continuation from 18 March through 31 December. Every five-minute UTC
slot in 2025 has exactly one catalogue entry with the expected outcome,
ingestion signature and selected source path where applicable.

| Catalogue outcome | Fields |
| --- | ---: |
| Data payload | 58,183 |
| Explicit negligible-rain zero | 43,157 |
| Missing source slot | 3,779 |
| Selected observation classified all-NaN | 1 |
| Total | 105,120 |

Available coverage is **96.4041%** (101,340 data/zero fields). Missing entries
remain unavailable, not dry. The all-NaN outcome occurs at
2025-09-11 18:25 UTC. The accepted storage and CRS-rounding policies are unchanged.

Two ingestion signatures cover the pre-cache and cached processing periods;
these match their respective daily reports. Cache equivalence was established
when that optimisation was introduced; the current review did not repeat that
benchmark or reprocess every source field.

Every data entry references an existing GridFS payload with matching ID and
filename. One data and one zero field per month (24 fields total) were decoded:
all had the expected 250 × 250 shape, no infinite or negative valid values,
and zero fields reconstructed as zero over their valid support. This was a
full catalogue/report reconciliation with sampled decoded-field checks, not a
full raw-file hash audit or pixel-by-pixel reprocessing audit.

Detailed checks, daily-report hashes and sampled filenames are recorded in the
external [ingestion review](../../../data/alan/psteps_advect_data/runs/storms/20261122_ingestion_review/ingestion_review.json).

## Frozen storm population

Generated using the existing core storm publisher, with the database definition
verified against [the local configuration](../configs/db/config_bom_data.json).

- Database: `BOM`; collections: `storms` and `storm_generations`.
- Definition: `syd_1km_qpe_standard_v1`.
- Published generation: `2417c6c4be66438caac46c416efe770a`.
- Algorithm: `wet_intervals_v3`.
- Strict WAR threshold: greater than 0.025.
- Minimum elapsed storm span: 6 hours.
- Maximum intervening dry gap: 30 minutes.
- Unavailable observations split events regardless of dry-gap allowance.

The published population contains **107 storms**, spanning 1,918.9167 hours
between their endpoints and 23,134 inclusive five-minute frame slots. Individual
event spans range from 6 to 77.5833 hours. The first starts on 5 January at
02:30 UTC; the last ends on 21 December at 21:35 UTC. These spans include
permitted dry intervals; they are not totals of wet-only hours.

Read-back verification confirmed that all records share the published generation
and that all 107 intervals exactly match an independently constructed grouping
of catalogue wet times and missing-slot boundaries. The generation record
includes the definition, core commit, relevant source-code hashes and ingestion
review hash. Preserve this generation identity for downstream event processing.

External provenance and snapshots:

- [Storm generation and verification](../../../data/alan/psteps_advect_data/runs/storms/20261122_ingestion_review/storm_generation.json).
- [Published storm records](../../../data/alan/psteps_advect_data/runs/storms/20261122_ingestion_review/storms.json).

Only the storm population was generated in this step. LK extraction,
non-Lagrangian parameter estimation and representative-event selection remain
subsequent work.
