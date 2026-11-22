"""Restartable Rainfields3 date-range ingestion; missing rainfall remains missing."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import re
import tempfile

import netCDF4

import numpy as np
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
import rasterio
import xarray as xr

UTC = timezone.utc
LOG = logging.getLogger(__name__)
DEFAULT_ROOT = Path('~/data/alan/psteps_advect_data').expanduser()


@lru_cache(maxsize=32)
def _crs_from_cf(serialised_attributes):
    """Cache complete CF definitions; projection changes get distinct entries."""
    return CRS.from_cf(json.loads(serialised_attributes))


def parse_source_crs(attributes):
    serialised = json.dumps(attributes, sort_keys=True,
                            default=lambda value: np.asarray(value).tolist())
    return _crs_from_cf(serialised)


@lru_cache(maxsize=32)
def target_crs(definition):
    """Parse the configured target once per distinct CRS definition."""
    return CRS.from_user_input(definition)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    part.write_text(json.dumps(value, indent=2, default=str) + '\n')
    part.replace(path)


def nominal_time(actual: datetime) -> datetime:
    """Nearest epoch-aligned five-minute slot; exact ties choose the earlier slot."""
    seconds = int(actual.timestamp())
    slot, offset = divmod(seconds, 300)
    return datetime.fromtimestamp((slot + (offset > 150)) * 300, UTC)


def discover(root: Path, start: date, end: date, radar: int, tolerance: float):
    """Select deterministic closest observations, including neighbouring-day files.

    Boundary days supply candidates that round into the requested UTC interval.
    Filenames provide selection times; selected files must agree internally.
    """
    first = datetime.combine(start, datetime.min.time(), UTC)
    last = datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC)
    pattern = re.compile(rf'{radar}_(\d{{8}}_\d{{6}})\.prcp-crate\.nc')
    grouped = defaultdict(list)
    rejected = []
    day = start - timedelta(days=1)
    while day <= end + timedelta(days=1):
        directory = root / str(day.year) / f'{day:%m}' / f'{day:%d}'
        for path in sorted(directory.glob('*.nc')):
            match = pattern.fullmatch(path.name)
            if not match:
                raise ValueError(f'Unexpected source filename: {path}')
            actual = datetime.strptime(match[1], '%Y%m%d_%H%M%S').replace(tzinfo=UTC)
            if actual.date() != day:
                raise ValueError(f'Source date disagrees with directory: {path}')
            slot = nominal_time(actual)
            if first <= slot < last:
                offset = abs((actual - slot).total_seconds())
                record = {'path': str(path.resolve()), 'source_time': actual.isoformat(),
                          'valid_time': slot.isoformat(), 'offset_seconds': (actual-slot).total_seconds()}
                if offset <= tolerance:
                    grouped[slot].append(record)
                else:
                    rejected.append(record)
        day += timedelta(days=1)
    selected = {}
    collisions = []
    for slot, candidates in grouped.items():
        ordered = sorted(candidates, key=lambda r: (abs(r['offset_seconds']), r['source_time'], r['path']))
        selected[slot] = ordered[0]
        if len(ordered) > 1:
            collisions.append({'valid_time': slot.isoformat(), 'selected': ordered[0], 'discarded': ordered[1:]})
    return selected, {'rejected': rejected, 'collisions': collisions,
                      'selected': len(selected), 'expected': int((last-first).total_seconds()/300)}


def _axis_metres(coord):
    values = np.asarray(coord.values, dtype=float)
    scale = {'m': 1., 'km': 1000.}.get(coord.attrs.get('units'))
    if scale is None:
        raise ValueError('Projection coordinates must declare m or km')
    values = values * scale
    if values.ndim != 1 or values.size < 2 or not np.isfinite(values).all():
        raise ValueError('Expected finite one-dimensional coordinates')
    spacing = np.diff(values)
    if not (np.all(spacing > 0) or np.all(spacing < 0)) or not np.allclose(spacing, spacing[0], atol=1e-6, rtol=1e-9):
        raise ValueError('Source grid must be regular and strictly monotonic')
    return values, abs(float(spacing[0]))


def read_native(path: Path, radar: int):
    """Decode calibrated rates and CF masks; reject mismatched source metadata."""
    before = sha256(path)
    with xr.open_dataset(path, decode_times=True, mask_and_scale=True) as ds:
        if int(ds.attrs.get('station_id', -1)) != radar:
            raise ValueError('Unexpected radar station')
        if 'rain_rate' not in ds or set(ds.rain_rate.dims) != {'y', 'x'}:
            raise ValueError('Expected a two-dimensional rain_rate(y, x) field')
        if ds.rain_rate.attrs.get('units') not in ('mm hr-1', 'mm h-1', 'mm/h'):
            raise ValueError('Unsupported rain-rate units; no depth-to-rate conversion is allowed')
        if ds.valid_time.size != 1:
            raise ValueError('Expected one observation time per file')
        actual = datetime.fromisoformat(np.datetime_as_string(np.asarray(ds.valid_time.values).reshape(-1)[0], unit='s')).replace(tzinfo=UTC)
        if np.isnat(np.asarray(ds.valid_time.values).reshape(-1)[0]):
            raise ValueError('Missing observation time')
        expected = datetime.strptime(path.name.split('.')[0].split('_', 1)[1], '%Y%m%d_%H%M%S').replace(tzinfo=UTC)
        if actual != expected:
            raise ValueError('Filename and internal valid_time disagree')
        grid_mapping = ds.rain_rate.attrs.get('grid_mapping')
        if grid_mapping not in ds:
            raise ValueError('Missing CF grid mapping')
        attrs = dict(ds[grid_mapping].attrs)
        crs = parse_source_crs(attrs)
        if not crs.is_projected:
            raise ValueError('Rainfields3 source must use a projected CRS')
        x, dx = _axis_metres(ds.x)
        y, dy = _axis_metres(ds.y)
        values = np.array(ds.rain_rate.transpose('y', 'x').values, dtype=float, copy=True)
        # GDAL transforms describe pixel edges; arrays passed to GDAL are north-up.
        if x[0] > x[-1]:
            x, values = x[::-1], values[:, ::-1]
        if y[0] < y[-1]:
            y, values = y[::-1], values[::-1, :]
        if np.isinf(values).any() or np.any(values[np.isfinite(values)] < 0):
            raise ValueError('Decoded rain rates must be nonnegative or missing')
        transform = from_origin(x[0]-dx/2, y[0]+dy/2, dx, dy)
        # Confirm supplied cell bounds agree with centre-derived geometry.
        for name, axis, step in [('x', x, dx), ('y', y, dy)]:
            bounds_name = ds[name].attrs.get('bounds')
            if bounds_name not in ds:
                raise ValueError(f'Missing {name} cell bounds')
            scale = 1000. if ds[name].attrs['units'] == 'km' else 1.
            bounds = np.sort(np.asarray(ds[bounds_name].values, float).reshape(-1)*scale)
            centres = np.asarray(ds[name].values, float)*scale
            expected_bounds = np.sort(np.column_stack((centres-step/2, centres+step/2)).reshape(-1))
            if not np.allclose(bounds, expected_bounds, atol=1e-6, rtol=0):
                raise ValueError(f'Inconsistent {name} cell bounds')
    if sha256(path) != before:
        raise ValueError('Source changed while reading')
    return values, transform, crs, attrs, actual, before


def accepted_source_crs(source_crs: CRS, attrs: dict, target: CRS):
    """Normalise only the approved rounded Terry Hills central-meridian variant."""
    normalised = False
    effective = source_crs
    if attrs.get('grid_mapping_name') == 'albers_conical_equal_area' and attrs.get('longitude_of_central_meridian') == 151.209:
        candidate = parse_source_crs({**attrs, 'longitude_of_central_meridian': 151.2094})
        if candidate.equals(target):
            effective, normalised = target, True
    return effective, normalised


def remap(values, transform, source_crs, domain):
    """GDAL area averaging with strict valid-area coverage, at arbitrary spacing.

    Warp zero-filled rainfall AND a 0/1 validity band over the same footprints.
    Missing source cells are never excluded from the denominator: any missing
    contribution invalidates the destination. A separate coverage band rejects
    target cells not completely inside the source footprint.
    """
    target = target_crs(domain['projection']['epsg'])
    step = float(domain['p_size'])
    nr, nc = int(domain['n_rows']), int(domain['n_cols'])
    if step <= 0 or min(nr, nc) < 2:
        raise ValueError('Invalid destination domain')
    top = float(domain['start_y']) + (nr-1)*step + step/2
    left = float(domain['start_x']) - step/2
    destination_transform = from_origin(left, top, step, step)
    def warp(source, src_transform=transform):
        destination = np.full((nr,nc), np.nan, dtype='float64')
        reproject(source=np.asarray(source, dtype='float64'), destination=destination,
                  src_transform=src_transform, src_crs=source_crs,
                  dst_transform=destination_transform, dst_crs=target,
                  src_nodata=None, dst_nodata=np.nan, resampling=Resampling.average,
                  num_threads=1, init_dest_nodata=True)
        return destination
    valid = np.isfinite(values)
    rate = warp(np.where(valid, values, 0.))
    fraction = warp(valid.astype(float))
    # Add one ring of explicit zero coverage: GDAL average alone can otherwise
    # renormalise a partially out-of-bounds target cell over the available area.
    coverage = np.pad(np.ones(values.shape), 1)
    coverage[[0,-1],:] = 0.; coverage[:,[0,-1]] = 0.
    padded_transform = from_origin(transform.c-transform.a, transform.f-transform.e,
                                   transform.a, -transform.e)
    covered = warp(coverage, padded_transform)
    rate[(fraction < 1.-1e-8) | (covered < 1.-1e-8) | ~np.isfinite(fraction) | ~np.isfinite(covered)] = np.nan
    # Database coordinates ascend south to north.
    return xr.DataArray(rate[::-1].copy(), dims=('y','x'),
        coords={'x': float(domain['start_x'])+np.arange(nc)*step,
                'y': float(domain['start_y'])+np.arange(nr)*step},
        attrs={'units':'mm/h','projection':target.to_wkt(), 'long_name':'Bias corrected rainfall rate'})


def prepare(candidate, domain, radar):
    path = Path(candidate['path'])
    values, transform, original_crs, attrs, actual, checksum = read_native(path, radar)
    if actual.isoformat() != candidate['source_time']:
        raise ValueError('Selected source time changed')
    effective, normalised = accepted_source_crs(original_crs, attrs, target_crs(domain['projection']['epsg']))
    field = remap(values, transform, effective, domain)
    validate_packing(field.values)
    provenance = {'path':str(path), 'sha256':checksum, 'source_valid_time':actual.isoformat(),
                  'source_crs_wkt':original_crs.to_wkt(), 'effective_crs_wkt':effective.to_wkt(),
                  'central_meridian_normalised':normalised, 'offset_seconds':candidate['offset_seconds']}
    return field, provenance



def validate_packing(values):
    """Guard the shared encoder before either dry-run acceptance or storage."""
    from pysteps_param.db.core.nc_utils import RAIN_SCALE_FACTOR, RAIN_FILL_VALUE
    observed = np.asarray(values)[np.isfinite(values)]
    codes = np.rint(observed / RAIN_SCALE_FACTOR)
    if codes.size and (codes.min() < np.iinfo(np.int16).min
                       or codes.max() > np.iinfo(np.int16).max
                       or np.any(codes == RAIN_FILL_VALUE)):
        raise ValueError('Rain rate exceeds the core NetCDF packing range')


def runtime_identity():
    import pysteps_param
    core = Path(pysteps_param.__file__).resolve().parent
    modules = ['db/core/gridfs_io.py','db/core/field_catalogue.py','db/core/field_masks.py','db/core/nc_utils.py','storage_policy.py']
    return {'reader_sha256':sha256(Path(__file__)),
            'core_sources':{p:sha256(core/p) for p in modules},
            'versions':{p:version(p) for p in ('pysteps-param','pysteps-param-bom-cases','numpy','xarray','netCDF4','pyproj','rasterio')},
            'gdal':rasterio.__gdal_version__}



def encode_field(field, nominal, source):
    """Use core packing, replacing its WRNZ-specific global source attribution."""
    from pysteps_param import write_netcdf_buffer
    from pysteps_param.db.core.nc_utils import RainFieldBuffer
    packed = write_netcdf_buffer(field, nominal)
    # The core writer currently hard-codes WRNZ as institution. Amend only global
    # attributes on a private temporary copy; retain the core payload and mask.
    with tempfile.NamedTemporaryFile(suffix='.nc') as temporary:
        temporary.write(packed.getvalue())
        temporary.flush()
        with netCDF4.Dataset(temporary.name, 'r+') as dataset:
            dataset.institution = 'Commonwealth of Australia, Bureau of Meteorology'
            dataset.source = 'Bureau Rainfields3 calibrated radar rain rate; processed by pysteps_param_bom_cases'
            dataset.source_file = source['path']
            dataset.source_sha256 = source['sha256']
            dataset.source_valid_time = source['source_valid_time']
            dataset.comment = 'Nominal five-minute time; actual source time and processing provenance retained in catalogue.'
        payload = Path(temporary.name).read_bytes()
    return RainFieldBuffer(payload, field.values)


def ingest_one(db, candidate, domain, product, policy, signature, radar, replace=False):
    from pysteps_param import make_nc_name, write_rainfield, get_rainfield
    from pysteps_param.storage_policy import classify_rainfall
    nominal = datetime.fromisoformat(candidate['valid_time'])
    filename = make_nc_name(domain['name'],product['product'],nominal)
    previous = db.field_catalogue.find_one({'filename':filename})
    checksum = sha256(Path(candidate['path']))
    if previous and not replace:
        meta = previous['metadata']
        if (meta.get('ingestion_signature') != signature or meta.get('source',{}).get('sha256') != checksum
            or meta.get('source',{}).get('source_valid_time') != candidate['source_time']):
            raise ValueError('Existing field has different source/configuration; explicit --replace required')
        if previous['status'] in ('data','zero'):
            get_rainfield(db,filename,missing_as_nan=True)
            return 'reused'
        if previous.get('reason')=='all_nan':
            return 'reused_missing'
    field, source = prepare(candidate,domain,radar)
    if source['sha256']!=checksum:
        raise ValueError('Source changed since restart check')
    values = field.values
    observed = values[np.isfinite(values)]
    status,_ = classify_rainfall(values,policy)
    metadata = {'domain':domain['name'],'product':product['product'],'valid_time':nominal,
                'base_time':None,'ensemble':None,'forecast_lead_time':None,'units':'mm/h',
                'threshold_mmph':float(product['rain_threshold']),
                'valid_fraction':float(np.isfinite(values).mean()),
                'wetted_area_ratio':float(np.count_nonzero(np.isfinite(values)&(values>=product['rain_threshold']))/values.size),
                'mean':float(observed.mean()) if observed.size else None,
                'std_dev':float(observed.std()) if observed.size else None,
                'max':float(observed.max()) if observed.size else None,
                'source':source,'ingestion_signature':signature,
                'regridding':{'method':'gdal_average','missing':'full_valid_coverage','crs_policy':'terry_hills_rounding_v1'}}
    write_rainfield(db,filename,encode_field(field,nominal,source),metadata,
                   storage_policy=policy,rain_values=values)
    record=db.field_catalogue.find_one({'filename':filename})
    if record is None or record['status']!=status:
        raise RuntimeError('Published catalogue status differs from expected classification')
    return status


def build_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('start_date',type=date.fromisoformat)
    p.add_argument('end_date',type=date.fromisoformat)
    p.add_argument('--data-root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--config',type=Path,default=Path(__file__).resolve().parents[2]/'configs/db/config_bom_data.json')
    p.add_argument('--db',default='BOM')
    p.add_argument('--domain',default='syd_1km')
    p.add_argument('--product',default='qpe')
    p.add_argument('--radar',type=int,default=71)
    p.add_argument('--time-tolerance-seconds',type=float,default=150.)
    p.add_argument('--inventory-only',action='store_true',help='Filename selection only; no pixel validation or database access')
    p.add_argument('--dry-run',action='store_true',help='Read/regrid/classify selected fields without database access')
    p.add_argument('--replace',action='store_true',help='Explicitly replace conflicting records; downstream products need regeneration')
    p.add_argument('--report-dir',type=Path,help='Write a run manifest and one restart-safe report per UTC day')
    return p


def main(argv=None):
    p=build_parser();args=p.parse_args(argv)
    if args.end_date<args.start_date or not 0<=args.time_tolerance_seconds<=150 or args.radar<=0:
        p.error('Use ordered dates, a positive radar and a tolerance between 0 and 150 seconds')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    config=json.loads(args.config.read_text())
    domain=next(d for d in config['domains'] if d['name']==args.domain)
    product=next(d for d in config['inputs'] if d['domain']==args.domain and d['product']==args.product)
    policy=next(d for d in config['rainfield_storage_policies'] if d['default'])
    if product['kind']!='analysis' or product['timestep']!=300:
        p.error('This reader requires an analysis product with a 300 second timestep')
    from pysteps_param.storage_policy import prepare_storage_policy, classify_rainfall
    policy=prepare_storage_policy(policy)
    root=args.data_root.expanduser().resolve()/'rainfields/nci_rainfields3'/str(args.radar)
    if not root.is_dir():
        p.error(f'Native radar data directory does not exist: {root}')
    selected,inventory=discover(root,args.start_date,args.end_date,args.radar,args.time_tolerance_seconds)
    print(json.dumps({'inventory':{'selected':inventory['selected'],
          'expected':inventory['expected'], 'collision_slots':len(inventory['collisions']),
          'rejected':len(inventory['rejected']),
          'off_grid_selected':sum(c['offset_seconds'] != 0 for c in selected.values())}}),flush=True)
    if args.inventory_only:
        if args.report_dir:
            atomic_json(args.report_dir/'inventory.json',inventory)
        return 0
    identity={'domain':domain,'product':product,'storage_policy':policy,
              'time_policy':{'nearest_seconds':300,'tolerance_seconds':args.time_tolerance_seconds,'ties':'earlier'},
              'spatial_policy':{'method':'gdal_average','missing':'full_valid_coverage','crs_rounding':'terry_hills_rounding_v1'},
              'runtime':runtime_identity()}
    signature=fingerprint(identity)
    if args.report_dir is None and not args.dry_run:
        args.report_dir=args.data_root.expanduser()/'runs/ingestion'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S_%fZ')
    if args.report_dir:
        existing=args.report_dir/'run.json'
        if existing.exists():
            previous_run=json.loads(existing.read_text())
            if (previous_run['signature']!=signature or previous_run['start']!=str(args.start_date)
                or previous_run['end']!=str(args.end_date) or previous_run['dry_run']!=args.dry_run):
                raise ValueError('Report directory belongs to a different ingestion run')
        atomic_json(existing,{'signature':signature,'configuration':identity,'inventory':inventory,
                             'start':str(args.start_date),'end':str(args.end_date),'dry_run':args.dry_run})
    if args.report_dir:
        LOG.info('Reports: %s',args.report_dir.resolve())
    db=None
    if not args.dry_run:
        from pysteps_param import get_db
        from pysteps_param.storage_policy import resolve_storage_policy
        db=get_db(target_db=args.db)
        if db.client.address[0] not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Expected local database; refusing an unexpected remote endpoint')
        for query,expected in [({'kind':'domain','name':args.domain},domain),
                               ({'kind':'analysis','domain':args.domain,'product':args.product},product)]:
            stored=db.config.find_one(query,sort=[('inserted_at',-1)])
            if stored is None or any(stored.get(k)!=v for k,v in expected.items()):
                raise ValueError('Local and database configurations disagree')
        if resolve_storage_policy(db)!=policy:
            raise ValueError('Database storage policy differs from the configuration file')
        import fcntl
        lock_dir=args.data_root.expanduser()/'runs/ingestion_locks'
        lock_dir.mkdir(parents=True,exist_ok=True)
        lock_file=(lock_dir/(fingerprint([args.db,args.domain,args.product])+'.lock')).open('a')
        fcntl.flock(lock_file,fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep lock_file open until the command exits. A crash releases the lock.
        if 'field_identity_unique_v1' not in db.field_catalogue.index_information():
            raise ValueError('Initialise the target database indexes before ingestion')
    failures=0;totals=Counter();day=args.start_date
    while day<=args.end_date:
        records=[]
        for i in range(288):
            slot=datetime.combine(day,datetime.min.time(),UTC)+timedelta(minutes=i*5)
            candidate=selected.get(slot)
            result={'valid_time':slot.isoformat()}
            try:
                if candidate is None:
                    result['status']='source_missing'
                    # Absence remains unavailable; do not overwrite an existing field.
                    if db is not None:
                        from pysteps_param import make_nc_name
                        from pysteps_param.db.core.field_catalogue import write_missing_field
                        name=make_nc_name(args.domain,args.product,slot)
                        prior=db.field_catalogue.find_one({'filename':name})
                        if prior is None:
                            write_missing_field(db,name,{'domain':args.domain,'product':args.product,
                                'valid_time':slot,'base_time':None,'ensemble':None,
                                'ingestion_signature':signature},reason='source_slot_absent')
                        else:
                            result['status']='existing_without_source'
                else:
                    result.update(candidate)
                    if args.dry_run:
                        field,source=prepare(candidate,domain,args.radar)
                        result.update(status=classify_rainfall(field.values,policy)[0],source=source,
                                      valid_fraction=float(np.isfinite(field.values).mean()))
                    else:
                        result['status']=ingest_one(db,candidate,domain,product,policy,signature,args.radar,args.replace)
            except Exception as exc:
                failures+=1;result.update(status='failed',error=f'{type(exc).__name__}: {exc}')
                LOG.error('%s: %s',slot,result['error'])
            records.append(result);totals[result['status']]+=1
        if args.report_dir:
            atomic_json(args.report_dir/f'{day}.json',{'date':str(day),'signature':signature,'records':records})
        LOG.info('%s: %s',day,dict(Counter(r['status'] for r in records)))
        day+=timedelta(days=1)
    summary={'signature':signature,'counts':dict(totals),'failures':failures,
             'finished_utc':datetime.now(UTC).isoformat()}
    if args.report_dir:
        atomic_json(args.report_dir/'summary.json',summary)
    print(json.dumps(summary),flush=True)
    return 1 if failures else 0
