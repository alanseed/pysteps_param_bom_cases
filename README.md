# Bureau radar cases for pysteps_param

Acquisition and processing workflows for Bureau of Meteorology radar data, with
archive ingestion into a pysteps_param database planned as a subsequent step.
Scientific experiment definitions and paper outputs belong in the separate
[psteps_advect_paper repository](https://github.com/alanseed/psteps_advect_paper).

## Layout

- `scripts/` — executable acquisition and processing entry points.
- `tests/` — integrity and scientific-contract checks.
- `configs/` — source, domain and product configuration (add as implemented).
- `run/` — repeatable archive runners (add as implemented).
- `docs/` — source formats, licences and processing contracts.
- `src/pysteps_param_bom_cases/` — future reusable Bureau adapters, added when needed.

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


## Validation

```bash
python3 -m unittest discover -s tests -v
```

See [Rainfields3 source documentation](docs/rainfields3.md). Database ingestion,
regridding and archive runners are not implemented yet. Reuse suitable shared
components from pysteps_param and its operations package after validating their
contracts against Bureau data; the WRNZ QPE reader assumes rainfall depth and
must not be used unchanged for these rain-rate files.
