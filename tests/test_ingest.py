"""Independent geometry, mask, time-selection and restart checks."""
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path

import numpy as np
from pyproj import CRS
import pytest
from rasterio.transform import from_origin
import xarray as xr

from pysteps_param_bom_cases.ingest import (
    accepted_source_crs, discover, ingest_one, nominal_time, prepare, read_native, remap,
)

UTC=timezone.utc
CRS_ATTRS={'grid_mapping_name':'albers_conical_equal_area','standard_parallel':[-32.2,-35.2],
           'longitude_of_central_meridian':151.2094,'latitude_of_projection_origin':-33.7008,
           'false_easting':0.,'false_northing':0.,'semi_major_axis':6378137.,'semi_minor_axis':6356752.31414}
CRS_NATIVE=CRS.from_cf(CRS_ATTRS)


def domain(start=500.,step=1000.,n=2):
    return {'name':'test','n_rows':n,'n_cols':n,'p_size':step,'start_x':start,'start_y':start,
            'projection':{'epsg':CRS_NATIVE.to_wkt()}}


def native(path, values=None, attrs=None, actual='2025-01-01T00:00:00'):
    if values is None:values=np.arange(16,dtype=float).reshape(4,4)
    ds=xr.Dataset({'rain_rate':(('y','x'),values),
                   'valid_time':np.datetime64(actual),'proj':0,
                   'x_bounds':(('x','bounds'),[[0,.5],[.5,1],[1,1.5],[1.5,2]]),
                   'y_bounds':(('y','bounds'),[[2,1.5],[1.5,1],[1,.5],[.5,0]])},
                  coords={'x':[.25,.75,1.25,1.75],'y':[1.75,1.25,.75,.25]},
                  attrs={'station_id':71})
    ds.x.attrs={'units':'km','bounds':'x_bounds'};ds.y.attrs={'units':'km','bounds':'y_bounds'}
    ds.rain_rate.attrs={'units':'mm hr-1','grid_mapping':'proj'};ds.proj.attrs=attrs or CRS_ATTRS
    ds.to_netcdf(path,encoding={'rain_rate':{'dtype':'int16','scale_factor':.05,'_FillValue':-1}})


def test_aligned_average_matches_independent_block_means_and_orientation():
    a=np.arange(16,dtype=float).reshape(4,4)
    out=remap(a,from_origin(0,2000,500,500),CRS_NATIVE,domain())
    expected=np.array([[10.5,12.5],[2.5,4.5]])
    np.testing.assert_allclose(out.values,expected,atol=1e-9)
    np.testing.assert_array_equal(out.y,[500,1500])
    np.testing.assert_array_equal(out.x,[500,1500])


def test_noninteger_spacing_uses_area_weights():
    a=np.tile([0.,10.,20.,30.],(4,1))
    out=remap(a,from_origin(0,2000,500,500),CRS_NATIVE,domain(start=375,step=750))
    # First 750 m contains 500 m of value 0 and 250 m of value 10.
    # Next 750 m contains 250 m of value 10 and 500 m of value 20.
    np.testing.assert_allclose(out.values,np.tile([10/3,50/3],(2,1)),atol=1e-8)


def test_missing_contributor_invalidates_target_instead_of_renormalising():
    a=np.ones((4,4));a[0,0]=np.nan
    out=remap(a,from_origin(0,2000,500,500),CRS_NATIVE,domain())
    assert np.isnan(out.values[1,0])
    assert np.count_nonzero(np.isfinite(out))==3
    np.testing.assert_allclose(out.values[np.isfinite(out)],1)


def test_partial_source_footprint_is_missing():
    out=remap(np.ones((4,4)),from_origin(0,2000,500,500),CRS_NATIVE,domain(start=250))
    assert np.isnan(out.values[:,0]).all()
    assert np.isnan(out.values[0,:]).all()
    assert out.values[1,1]==pytest.approx(1)


def test_changed_crs_uses_real_coordinate_transformation():
    # Shift target false easting by 500 m: its coordinates map half a source
    # kilometre westward, giving known averages different from a naive crop.
    shifted=CRS.from_cf({**CRS_ATTRS,'false_easting':500.})
    target=domain(start=1000);target['start_y']=1500.;target['projection']['epsg']=shifted.to_wkt()
    out=remap(np.tile([0.,10.,20.,30.],(4,1)),from_origin(0,3000,500,500),CRS_NATIVE,target)
    assert out.values[0,0]==pytest.approx(5,abs=1e-6)
    assert out.values[0,1]==pytest.approx(25,abs=1e-6)


def test_normalisation_is_limited_to_approved_rounding():
    rounded={**CRS_ATTRS,'longitude_of_central_meridian':151.209}
    effective,normalised=accepted_source_crs(CRS.from_cf(rounded),rounded,CRS_NATIVE)
    assert normalised and effective.equals(CRS_NATIVE)
    changed={**rounded,'standard_parallel':[-31.,-35.2]}
    effective,normalised=accepted_source_crs(CRS.from_cf(changed),changed,CRS_NATIVE)
    assert not normalised and not effective.equals(CRS_NATIVE)


def test_reader_decodes_fill_and_checks_internal_time(tmp_path):
    path=tmp_path/'71_20250101_000000.prcp-crate.nc'
    a=np.ones((4,4));a[0,0]=np.nan
    native(path,a)
    result=read_native(path,71)
    assert np.isnan(result[0][0,0]);assert result[4]==datetime(2025,1,1,tzinfo=UTC)
    native(path,actual='2025-01-01T00:05:00')
    with pytest.raises(ValueError,match='internal valid_time'):read_native(path,71)


def test_time_selection_boundary_collision_and_tolerance(tmp_path):
    d=tmp_path/'2024/12/31';d.mkdir(parents=True)
    (d/'71_20241231_235900.prcp-crate.nc').touch()
    d=tmp_path/'2025/01/01';d.mkdir(parents=True)
    for t in ['000000','000100','000730']:(d/f'71_20250101_{t}.prcp-crate.nc').touch()
    selected,report=discover(tmp_path,date(2025,1,1),date(2025,1,1),71,150)
    first=selected[datetime(2025,1,1,tzinfo=UTC)]
    assert first['path'].endswith('000000.prcp-crate.nc')
    assert len(report['collisions'][0]['discarded'])==2
    assert selected[datetime(2025,1,1,0,5,tzinfo=UTC)]['offset_seconds']==150
    _,report=discover(tmp_path,date(2025,1,1),date(2025,1,1),71,0)
    assert len(report['rejected'])==3
    assert nominal_time(datetime(2025,1,1,0,7,30,tzinfo=UTC)).minute==5


def test_core_buffer_round_trip_preserves_mask_and_packing_tolerance(tmp_path):
    from pysteps_param import write_netcdf_buffer, read_netcdf_buffer
    path=tmp_path/'71_20250101_000000.prcp-crate.nc';a=np.arange(16.).reshape(4,4);a[0,0]=np.nan
    native(path,a)
    c={'path':str(path),'source_time':'2025-01-01T00:00:00+00:00','offset_seconds':0}
    field,_=prepare(c,domain(),71)
    buf=write_netcdf_buffer(field,datetime(2025,1,1,tzinfo=UTC))
    decoded,when=read_netcdf_buffer(buf.getvalue(),missing_as_nan=True)
    np.testing.assert_array_equal(np.isnan(decoded),np.isnan(field))
    np.testing.assert_allclose(decoded.values,field.values,atol=.050001,equal_nan=True)


def test_restart_checks_provenance_and_reads_existing_payload(tmp_path,monkeypatch):
    import mongomock
    import pysteps_param
    from pysteps_param import make_nc_name
    from pysteps_param_bom_cases.ingest import sha256
    p=tmp_path/'field.nc';p.write_bytes(b'unchanged')
    t=datetime(2025,1,1,tzinfo=UTC);name=make_nc_name('test','qpe',t)
    c={'path':str(p),'valid_time':t.isoformat(),'source_time':t.isoformat()}
    db=mongomock.MongoClient().db
    db.field_catalogue.insert_one({'filename':name,'status':'data','metadata':{
        'ingestion_signature':'same','source':{'sha256':sha256(p),'source_valid_time':t.isoformat()}}})
    calls=[];monkeypatch.setattr(pysteps_param,'get_rainfield',lambda *a,**k:calls.append(k))
    assert ingest_one(db,c,domain(),{'product':'qpe'},None,'same',71)=='reused'
    assert calls==[{'missing_as_nan':True}]
    with pytest.raises(ValueError,match='different source/configuration'):
        ingest_one(db,c,domain(),{'product':'qpe'},None,'different',71)


def test_encoding_retains_bureau_attribution_and_prepacking_rates(tmp_path):
    import netCDF4
    from pysteps_param_bom_cases.ingest import encode_field
    field=xr.DataArray(np.array([[.15,np.nan],[0.,2.35]]),dims=('y','x'),
                       coords={'x':[500.,1500.],'y':[500.,1500.]},attrs={'projection':CRS_NATIVE.to_wkt()})
    source={'path':'example.nc','sha256':'hash','source_valid_time':'2025-01-01T00:01:00+00:00'}
    buf=encode_field(field,datetime(2025,1,1,tzinfo=UTC),source)
    np.testing.assert_allclose(buf.rain_values,field.values,equal_nan=True)
    with netCDF4.Dataset('memory',memory=buf.getvalue()) as ds:
        assert 'Bureau of Meteorology' in ds.institution
        assert ds.source_valid_time==source['source_valid_time']
        assert ds['rainfall'][0].mask[0,1]


@pytest.mark.parametrize('values,status',[(np.ones((4,4)),'data'),
                                         (np.zeros((4,4)),'zero'),
                                         (np.full((4,4),np.nan),'missing')])
def test_ingest_with_core_catalogue_and_gridfs_mock(tmp_path,values,status,monkeypatch):
    import mongomock
    import mongomock.gridfs
    mongomock.gridfs.enable_gridfs_integration()
    from pysteps_param import get_rainfield, make_nc_name
    from pysteps_param.db.core.field_catalogue import ensure_field_catalogue_indexes
    from pysteps_param_bom_cases.ingest import ingest_one
    db=mongomock.MongoClient().db
    from types import SimpleNamespace
    # PyMongo GridFS expects a client-options timeout absent from mongomock.
    monkeypatch.setattr(db.client, "options", SimpleNamespace(timeout=None), raising=False)
    d=domain();db.config.insert_one({**d,'kind':'domain'})
    ensure_field_catalogue_indexes(db)
    policy={'kind':'rainfield_storage_policy','name':'negligible','version':1,'default':True,
            'rain_threshold_mmph':.1,'minimum_fraction':.01,'comparison':'greater_than',
            'denominator':'entire_domain','classification_stage':'after_regridding_before_encoding'}
    path=tmp_path/'71_20250101_000000.prcp-crate.nc';native(path,values)
    t=datetime(2025,1,1,tzinfo=UTC)
    c={'path':str(path),'source_time':t.isoformat(),'valid_time':t.isoformat(),'offset_seconds':0.}
    assert ingest_one(db,c,d,{'product':'qpe','rain_threshold':1.},policy,'signature',71)==status
    record=db.field_catalogue.find_one()
    assert record['status']==status
    assert db['rain.files'].count_documents({})==int(status=='data')
    if status!='missing':
        field,_=get_rainfield(db,make_nc_name('test','qpe',t),missing_as_nan=True)
        np.testing.assert_allclose(field.values,1. if status=='data' else 0.)
    assert ingest_one(db,c,d,{'product':'qpe','rain_threshold':1.},policy,'signature',71).startswith('reused')


def test_packing_overflow_fails_before_encoding():
    from pysteps_param_bom_cases.ingest import validate_packing
    validate_packing(np.array([0.,3276.7,np.nan]))
    with pytest.raises(ValueError,match='packing range'):
        validate_packing(np.array([3276.8]))


def test_bad_data_root_fails_before_database_access(tmp_path,monkeypatch):
    import pysteps_param
    from pysteps_param_bom_cases.ingest import main
    monkeypatch.setattr(pysteps_param,'get_db',lambda **kw:pytest.fail('Must not connect to database'))
    with pytest.raises(SystemExit) as error:
        main(['2025-01-01','2025-01-01','--data-root',str(tmp_path)])
    assert error.value.code==2
