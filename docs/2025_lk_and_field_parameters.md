# Full 2025 event extraction and field parameters

## LK acceptance: 22 November 2026

The completed `syd_2025_fixed_lk_v1` run in `BOM_advection` was verified against
the frozen 107-storm generation `2417c6c4be66438caac46c416efe770a`.

| Outcome | Count |
| --- | ---: |
| Planned and stored input pairs | 23,134 |
| Vector results | 23,112 |
| Unavailable predecessor | 22 |
| Detected features | 3,527,122 |
| Successfully tracked vectors | 3,522,400 |
| Accepted vectors | 3,454,812 |
| Rejected outliers | 67,588 |

All 107 event records report completion. The run records the adopted
1,000-feature detection limit. Its selected input snapshot is unchanged.
Every vector payload was read back and checked for its checksum, detected/
tracked/accepted/rejected counts, projected-coordinate conversion and actual-
interval velocity conversion. All pair identities matched the expected
population, and all 22 unavailable outcomes corresponded to unavailable
supporting input. All current fields have complete spatial support.

Compressed array payloads total 138,476,850 bytes. No unreferenced completed
GridFS uploads were found. Recorded extraction time was 350.8 seconds.
The [detailed acceptance report](../../../data/alan/psteps_advect_data/runs/event_lk/syd_2025_fixed_lk_v1/acceptance.json)
retains the configuration identity and audited counts. This verifies persisted
outputs and their conventions; it does not establish tracking accuracy against
known truth.

## Field-parameter run

The [non-Lagrangian field-parameter stage](field_parameters.md) uses all 23,134
current event fields, including those with unavailable predecessors. Its
production identity is `syd_2025_fields_v1`. It completed on 22 November 2026
in 197.2 seconds, with the following verified outcomes:

| Outcome | Fields |
| --- | ---: |
| Stored parameter records | 23,134 |
| Defined latent Normal fits | 23,134 |
| Available spectral fits | 23,133 |
| Spectrum withheld for insufficient rain area | 1 |
| Processing failures | 0 |

The withheld spectrum is for 7 March 2025 at 18:15 UTC: WAR is 0.004208,
below the existing 0.01 spectral-estimation threshold. Its rainfall marginals
and latent fit remain available. This is a valid field inside an event's
permitted dry interval, not an unavailable observation or processing failure.

Read-back acceptance reconciled all field identities with the expected
107-storm population and unchanged source snapshot, including all 22 current
fields with unavailable predecessors. Every stored result passed support,
count/range and fit-status checks; available spectral arrays and model slopes
were finite. Independent checks decoded two fields per month, selected at the
minimum and maximum observed event-field rain fractions, and confirmed
physical rainfall moments, rain fraction and ordinary wet dBR mean.

The results are stored in `BOM_advection.field_parameters`, with the manifest
and summary in `BOM_advection.field_parameter_runs`. External records:

- [Estimator manifest](../../../data/alan/psteps_advect_data/runs/field_parameters/syd_2025_fields_v1/manifest.json).
- [Run summary](../../../data/alan/psteps_advect_data/runs/field_parameters/syd_2025_fields_v1/summary.json).
- [Acceptance report](../../../data/alan/psteps_advect_data/runs/field_parameters/syd_2025_fields_v1/acceptance.json).

Estimator source snapshots are retained alongside the reports and match the
recorded source hashes. Field-parameter tests and existing repository tests
passed (41 tests total). No Lagrangian temporal parameters or event-level
descriptor/tercile selection were generated in this step.
