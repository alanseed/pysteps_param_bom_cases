# Bureau radar cases for pysteps_param

Acquisition and processing workflows for Bureau of Meteorology radar data, with
archive ingestion, event-based LK extraction and field-parameter processing.
Scientific experiment definitions and paper outputs belong in the separate
[psteps_advect_paper repository](https://github.com/alanseed/psteps_advect_paper).

## Layout

- `scripts/` — executable acquisition and processing entry points.
- `tests/` — integrity and scientific-contract checks.
- `configs/` — source, domain and product configuration (add as implemented).
- `run/` — repeatable archive runners (add as implemented).
- `docs/` — source formats, licences and processing contracts.
- `src/pysteps_param_bom_cases/` — Bureau source decoding, remapping and ingestion.

Bulk data live outside Git at `~/data/alan/psteps_advect_data/`:

```text
psteps_advect_data/
├── metadata/nci_rainfields3/                 # source guide, README, licence
├── raw/nci_rainfields3/71/YYYY/prcp-crate/   # original daily ZIPs
├── rainfields/nci_rainfields3/71/YYYY/MM/DD/ # native NetCDF files
├── manifests/nci_rainfields3/71/YYYY/        # catalogue, inventory, daily hashes/gaps
├── processed/                              # future versioned grid/mask products
└── runs/<experiment>/<run-id>/              # future arrays, diagnostics and logs
```

## Acquire Terry Hills rain rate


Python 3.10+ is sufficient; acquisition uses only the standard library. Choose the
calendar year explicitly. The paper is provisionally considering 2025:

```bash
python3 scripts/fetch_nci_rainfields.py --year 2025 --dry-run
python3 scripts/fetch_nci_rainfields.py --year 2025 --start 2025-01-01 --end 2025-01-01
python3 scripts/fetch_nci_rainfields.py --year 2025
```

Radar defaults to 71 and data root to the directory above. Override with `--radar`
and `--data-root`. Dates are UTC and inclusive. Run one downloader per data root.
The dry run reads the catalogue without writing files. Full acquisition preserves
ZIPs, extracts native NetCDFs and records SHA-256 hashes. Restart with the same
command: completed days are hash-verified and reused, interrupted individual
transfers restart, and interrupted extraction reuses valid archives. An archive
that conflicts with a recorded hash is reported for investigation.

Exit status is nonzero for missing daily archives, failed transfers or missing /
off-grid five-minute timestamps. Individual failures do not stop subsequent days.
A successful transfer is not scientific acceptance: subsequently validate internal
time coordinates, rain-rate units, projection, masks and product changes across the
whole year. Timestamp-gap reports currently use filenames. No temporal resampling
or spatial aggregation is performed.


## Development environment

Use the dedicated `bom_cases` Conda environment and an editable installation of
the local pysteps_param checkout. See [environment setup](docs/environment.md)
for creation, installation and dependency policy.

## Validation

```bash
python3 -m unittest discover -s tests -v
```

See [Rainfields3 source documentation](docs/rainfields3.md) and the
[date-range ingestion workflow](docs/ingestion.md). The Bureau reader uses
Rasterio/GDAL remapping and the core storage API; the WRNZ accumulation reader
is not used for these calibrated rain-rate files.

## Sydney analysis grid

The configured `syd_1km` domain is a centred 250 × 250 grid at 1 km in the
native Terry Hills Albers projection. See [domain geometry and archive CRS
variation](docs/syd_1km_domain.md) before implementing ingestion.

## Configuration scope

[Database configuration](configs/db/config_bom_data.json) and the draft
[synthetic-event configuration](../psteps_advect_paper/configs/experiments/config_synthetic_event.json)
are separate. See [configuration scope and scientific sequence](docs/configuration_scope.md)
for loader compatibility and the planned progression from Sydney ingestion to
LK-vector analysis and synthetic tracking validation.

Archive processing is described in [LK runner usage](docs/event_lk_usage.md)
and [field-parameter processing](docs/field_parameters.md). See the
[2025 acceptance record](docs/2025_lk_and_field_parameters.md) for completed
archive products. Operational input/track QA remains in `figs/pilot*`.

Scientific analysis, synthetic configurations, methods and publication figures
now belong to [the paper repository](../psteps_advect_paper/README.md).
See the [migration record](docs/repository_migration.md) for the ownership
boundary and provenance/replay policy.

## Ingest an archive interval

```bash
conda activate bom_cases
python -m pip install --no-deps --no-build-isolation -e .
bom-ingest-rainfields 2025-01-01 2025-01-01 --dry-run
```

Remove `--dry-run` to write to the configured local `BOM` database. Read the
[ingestion policies and restart instructions](docs/ingestion.md) before running
a full interval.
