# Terry Hills analysis domain

`configs/db/config_bom_data.json` defines `syd_1km` as 250 rows by 250 columns,
with 1000 m cells centred on the native Terry Hills grid origin. This is a
250 km by 250 km footprint.

## Source geometry and target alignment

On 22 November 2026, one NetCDF per available extracted day was inspected
(361 daily representatives). All had 512 by 512 cells with 500 m spacing:

| Geometry | Native input | syd_1km target |
|---|---|---|
| Coordinate units | km | m |
| x cell centres | -127.75 to +127.75 km | -124500 to +124500 m |
| y cell centres | +127.75 to -127.75 km | -124500 to +124500 m |
| Cell-edge bounds, both axes | -128 to +128 km | -125000 to +125000 m |

The native y axis descends; the database domain generates ascending y coordinates.
Ingestion must reorder rows accordingly. The target origin fields `start_x` and
`start_y` are **cell centres**, both -124500 m, rather than cell edges.

Removing six 500 m cells at each side leaves the central 500 by 500 source
cells, whose 2 by 2 block centres coincide exactly with the target coordinates
when source and target CRS match. The resulting footprint has a 3 km margin
inside each native grid edge. This describes geometric alignment only: missing
pixel policy and aggregation implementation remain to be established.

## Bureau Albers Equal Area projection

The target retains the native CF projection parameters:

- Latitude of origin: -33.7008 degrees.
- Central meridian: 151.2094 degrees.
- Standard parallels: -32.2 and -35.2 degrees.
- False easting and northing: 0 m.
- Ellipsoid axes: 6378137.0 and 6356752.31414 m.

This is a site-specific Albers CRS. The existing core API reads the legacy
`projection.epsg` key using `pyproj.CRS.from_user_input`; that key therefore
contains the full WKT definition, not an invented EPSG identifier. The former
NZTM/EPSG:2193 configuration has been replaced. Source CF parameters are also
stored explicitly in the projection object.

## Archive metadata variation

Of the daily representative files, 217 use central meridian 151.2094 degrees
and 144 use 151.209 degrees. Other checked projection parameters and grid
coordinates agree. This is a roughly 37 m positional difference near the origin.
The target uses 151.2094, as present in the latest source files. Daily sampling
does not rule out changes within a day.

The study owner accepted the difference as metadata rounding. The ingestion
reader records normalisation to 151.2094 when the remaining CF parameters match
the target, and remaps using Rasterio/GDAL average resampling. See
[ingestion.md](ingestion.md) for the implemented policy and mask handling.
The initial domain configuration itself did not regrid or ingest rainfall.

Validation compared target coordinates from the installed core
`generate_geo_dict` against independently calculated source block centres,
checked target cell-edge bounds and verified target CRS equivalence to the
31 December 2025 native CF definition using pyproj.
