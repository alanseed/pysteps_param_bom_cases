# Rainfields3 date-range ingestion

## Install and run

Activate `bom_cases`, update its Conda dependencies if necessary, then install
this repository editable. The local pysteps_param core must already be installed:

```bash
conda activate bom_cases
conda env update -n bom_cases -f environment.yml
python -m pip install --no-deps --no-build-isolation -e .
```

Both `bom-ingest-rainfields` and `python scripts/ingest_rainfields.py` accept
inclusive UTC start and end dates. They traverse the extracted native files;
ZIP extraction remains the acquisition script's responsibility.

```bash
# Select observations without database access or decoding rainfall.
bom-ingest-rainfields 2025-01-01 2025-12-31 --inventory-only

# Decode, remap and classify one complete day without any database writes.
bom-ingest-rainfields 2025-01-01 2025-01-01 --dry-run \
  --report-dir "$HOME/data/alan/psteps_advect_data/runs/validation/jan01"

# Ingest a chosen interval into the already configured local BOM database.
bom-ingest-rainfields 2025-01-01 2025-01-31 --db BOM \
  --report-dir "$HOME/data/alan/psteps_advect_data/runs/ingestion/jan2025"
```

The data root defaults to `~/data/alan/psteps_advect_data`; override it with
`--data-root`. The native files are expected below
`rainfields/nci_rainfields3/71/YYYY/MM/DD/`. The script defaults to `syd_1km`,
`qpe` and radar 71; `--config` defaults to this checkout's database configuration.
Production ingestion creates a dated report directory under the data root when
`--report-dir` is omitted. Inventory-only and dry-run modes write reports only
when explicitly requested. These modes do not connect to MongoDB.

## Spatial processing

The reader decodes CF packing and fill values, verifies rain-rate units, radar ID,
filename/internal time agreement, monotonic regular coordinates and cell bounds.
It never converts timestep accumulation to rate. Source negative or infinite
rates are rejected, rather than clipped into dry observations.

The agreed `terry_hills_rounding_v1` policy normalises central meridian 151.209
to 151.2094 only when the other CF parameters match the target. Original and
effective WKT definitions and a normalisation flag are retained per field.
Other source CRSs are transformed as declared, without this normalisation.

Rasterio/GDAL `Resampling.average` maps the source pixel footprints onto the
configured target affine grid. It supports non-integer spacing ratios and CRS
transformations. There is no hard-coded 2 by 2 crop or reshape. The target's
configured coordinates are cell centres and the database y axis ascends.

Rainfall and validity indicators undergo the same area-averaging operation.
All contributing source area must be valid (within a numerical fraction tolerance
of 1e-8); otherwise the target cell is missing. A padded source-coverage band
also rejects partial edge coverage. Missing source area is never renormalised
away. This is GDAL's average resampling, not a claim of exact conservative
remapping between arbitrary different projections.

## Time assignment

Each filename time is assigned to the nearest epoch-anchored five-minute slot,
with exact halfway ties assigned to the earlier slot. The default maximum offset
is 150 seconds; narrow it with `--time-tolerance-seconds` (0 means exact slots
only). Read neighbouring days so scans rounding across midnight are considered.
The selected file must have an internal observation time identical to its name.

When scans compete for a slot, choose the smallest absolute time offset, then
the earlier source time, then the lexical path. Record every discarded candidate
and every tolerance rejection in the inventory. No temporal interpolation occurs.
Retain the actual source time, signed offset and source checksum per field.
A nominally five-minute pair can therefore have a different actual interval;
subsequent LK analysis must use or constrain the actual separation deliberately.

The 2025 filename inventory selected 101,341 nominal slots from 101,436 files:
95 collision slots and 127 selected off-grid observations. This is a time-selection
inventory, not pixel-validity acceptance. Invalid selected files fail explicitly;
the script does not silently fall back to a discarded candidate.

## Storage, restart and reports

Use the existing database-wide negligible rainfall policy after remapping and
before encoding: fewer than 1% of domain cells exceeding 0.1 mm/h is negligible.
Missing pixels remain in the denominator. Core `write_rainfield` publishes `data`,
`zero` (negligible rainfall over its saved mask) or `missing`. Unavailable slots
receive missing catalogue entries only if no record exists; existing fields
are preserved and reported as `existing_without_source`.

The core NetCDF encoder supplies packing and mask handling. The adapter guards
against int16 overflow and replaces its WRNZ-specific global institution with
Bureau attribution. The stored field has nominal time; its actual source time
is retained in both the catalogue metadata and payload global attributes.

Repeat the same command to resume. Existing fields are reused only when source
checksum, actual source time and ingestion signature agree, and available
payloads/masks can be read through the core API. The signature includes source
code hashes, domain/product/storage settings, time/spatial policies and library
versions. Reuse an existing report directory only with the same dates and mode.

Conflicting existing records fail unless `--replace` is explicit. Replacing
rainfall requires separately regenerating any derived cascades, flow and
parameters; this script does not invalidate them automatically. A local advisory
lock prevents simultaneous ingestion of the same database/domain/product using
the same data root. Do not run concurrent writers with different data roots.

The run manifest records configuration and collision inventory. Daily JSON
reports record every expected slot, selected source and outcome; summary.json
records final counts. A crash before a daily report is committed can leave
successfully stored fields; a restart discovers and verifies those records.
Source files are SHA-256 checked and checked for changes during reads.

Exit status 1 means at least one selected field failed. Expected archive gaps
and reported collisions do not themselves fail an ingestion run. Review report
counts as well as exit status: a missing day can finish successfully with 288
missing catalogue entries. This script does not download missing files or
replace outages with observed dry rainfall.

## Initial validation

The implementation passed 19 tests covering independent aligned/non-integer area
averages, a changed-CRS transformation, missing/edge coverage, CF decoding,
nominal-time collisions, core packing, attribution, provenance conflicts,
resumption and catalogue/GridFS round trips against mongomock. Real MongoDB
payload ingestion has not yet been exercised by these tests.

Two complete real-data days passed read/remap/classification dry runs without
failures: 1 January 2025 produced 123 retained and 165 negligible fields; 7 January
produced 288 retained fields. Real sample payload round trips preserved masks
and differed from unencoded averages by at most the expected 0.05 mm/h from
core 0.1 mm/h packing. Both CRS variants were covered. The `BOM` field catalogue
was checked and remained empty after validation.

Detailed validation reports are under the external data root at
`runs/ingestion_validation/`. These development checks establish ingestion
behaviour; they are not independent tracking validation for the scientific paper.

## CRS metadata caching and continuation

The reader caches complete CF CRS definitions and configured target definitions
in bounded process-local caches. The core NetCDF reader also caches conversion
from CF metadata to the existing projection representation, including expensive
negative EPSG searches for custom CRSs. Every source still undergoes checksum,
coordinate, mask and time validation. Rainfall arrays and masks are not cached.

The 22 November 2026 cache upgrade passed 20 Bureau tests and 23 focused core
tests. A 12-field comparison across both CRS variants produced identical
unpacked/decoded values, masks, times, attributes, source provenance and policy
decisions. The read/remap/encode/decode benchmark took 9.22 seconds before
caching, 1.19 seconds with cold caches and 0.39 seconds with warm caches. These
are pipeline measurements rather than full database-throughput estimates.

The original year-long ingestion was interrupted after its last complete daily
report, 17 March. Continuation began on 18 March with `--replace`, reprocessing
the incomplete day and preserving all completed days. This uses the existing
date-range and replacement options; no legacy-signature compatibility mechanism
was introduced. Old and new run manifests retain their own implementation
fingerprints, with equivalence evidence and continuation links under the external
data root at `runs/ingestion/cache_upgrade_20261122T040047Z/`.

The first two production continuation days (18–19 March) completed without
failures in approximately 11 and 10 seconds, versus the original run's roughly
226 seconds per day. All 576 expected catalogue entries for those two days were
verified with the new signature. This is early production throughput, not a
full-year completion estimate. Daily-memory batching was deferred because the
metadata-cache change removed the dominant repeated work without increasing
array memory requirements.
