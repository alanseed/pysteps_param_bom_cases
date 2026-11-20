# Rainfields3 source and acquisition contract

## Source checked on 20 November 2026

- [NCI Rainfields3 catalogue](https://thredds.nci.org.au/thredds/catalog/rq0/rainfields3/catalog.html).
- [Terry Hills (71) catalogue](https://thredds.nci.org.au/thredds/catalog/rq0/rainfields3/71/catalog.html).
- [Dataset DOI](https://doi.org/10.25914/DTTK-H476).
- [Archive README](https://thredds.nci.org.au/thredds/fileServer/rq0/rainfields3/rq0_rainfields3_readme.pdf).
- [Source licence](https://thredds.nci.org.au/thredds/fileServer/rq0/rainfields3/rq0-rainfields3-license-cc4-by-nc.pdf).

Use `prcp-crate` (rain rate per radar volume), not `prcp-m15` (15-minute
accumulation). The source README specifies 500 m spacing, an Albers conical
equal-area projection and UTC timestamps. Radar 71 has year catalogue entries
2019–2026; listing a year does not imply complete coverage.

A live sample of `71_20240101.prcp-crate.zip` contained 288 distinct rain-rate
NetCDF filenames plus `_file_list`. Its 01:15 UTC slot is absent and an
off-grid 01:19 UTC file is present.
This establishes sample cadence only; check every acquired day and internal time
coordinates before defining the study dataset. Grids remain at native resolution.

## Licensing and attribution

Openly downloadable Bureau/NCI data still have their own usage conditions.
The licence PDF identifies Creative Commons Attribution-NonCommercial 4.0
International (CC BY-NC 4.0). The linked Rainfields3 licence is separate from this repository's Apache code
licence. Retain and review the source licence before redistributing rainfall or
derivative datasets; do not describe the data as unrestricted open-source assets.
The downloader saves the source licence, README and support guide outside Git.

The archive README requests attribution to Soderholm, Protat and Jakob (2019),
Australian Operational Weather Radar Dataset, DOI 10.25914/5cb686a8d9450;
acknowledgement of NCI and the Australian Bureau of Meteorology; and Seed,
Duthie and Chumchean (2007), Rainfields, 33rd Conference on Radar Meteorology.
Use the collection DOI above as well, checking the current citation guidance
when preparing the paper.

