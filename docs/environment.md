# Bureau cases development environment

Use the dedicated `bom_cases` Conda environment. Python 3.12 matches the local
pysteps_param package's supported Python range. Conda-forge supplies the scientific,
NetCDF, mapping, OpenCV and upstream pySTEPS stack. Keep OpenCV managed by
Conda: its distribution metadata already satisfies the core headless OpenCV
requirement, so installing a second pip OpenCV wheel would conflict.
The core package is installed editable from a local checkout, so changes there
are immediately visible. This does not isolate core source edits: use a core Git
worktree if changes must be developed independently of WRNZ operations.

From this repository:

```bash
conda env create -f environment.yml
conda activate bom_cases
bash run/install_local_core.sh "$HOME/wrnz/pysteps_param"
python -m unittest discover -s tests -v
python scripts/fetch_nci_rainfields.py --year 2025 --dry-run
```

The dry run exits nonzero when daily archives are missing; this is a coverage
finding, not an environment failure. No database connection or credentials are
required for acquisition or these tests.

This environment uses upstream pySTEPS, separate from the WRNZ environment's
patched pySTEPS build. It uses the local Python pysteps_param implementation;
no separately compiled C++ ingestion tools are installed. The Bureau reader and date-range ingestion command are described in
[ingestion.md](ingestion.md). Rasterio/GDAL supplies average remapping.

Add dependencies to environment.yml when an implemented workflow requires them.
xESMF/ESMPy, NWP tooling and WRNZ-specific packages are not prerequisites
for acquisition. Check CRS and mask requirements before adding a regridding path.

The environment file states development requirements. Before scientific runs,
record exact resolved Conda/pip packages and the current core Git revision.
Editable source revisions can change without package version metadata changing;
record the Git hash and dirty status at run time.

## Initial verified environment

The initial core checkout was clean at
`b67c308337257660da912d2686b80f465d8e2245` (20 November 2026), installed from
`~/wrnz/pysteps_param`. Upstream pySTEPS is 1.21.5. No WRNZ addon packages or
operations addon have been installed in this environment.

[Resolved Conda packages](../configs/conda-linux-64.explicit.txt) capture the
initial Linux environment. [Python package inventory](../configs/pip-resolved.txt)
is an inspection record, not a pip installation recipe: Conda owns these binary
packages. For an exact platform-specific rebuild, create the environment from
the explicit Conda specification and then run the local-core installer:

```bash
conda create -n bom_cases --file configs/conda-linux-64.explicit.txt
conda activate bom_cases
bash run/install_local_core.sh "$HOME/wrnz/pysteps_param"
```

The initial checks passed the three acquisition tests and fifteen core classified
advection tests. This verifies environment compatibility, not Bureau-domain
scientific acceptance or database connectivity.

After installing the local core, install the Bureau adapters with
`python -m pip install --no-deps --no-build-isolation -e .` from this repository.
The resolved environment snapshots now include Rasterio for ingestion.
