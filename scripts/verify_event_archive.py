"""Independent read-back and native-consumer checks for Sydney archive products."""
import argparse
from collections import Counter
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import numpy as np
from pysteps_param import get_db, get_rainfield_np, get_transformer
from pysteps_param.db.core.state_io import get_cascade_state, get_oflow_state
from pysteps_param.optical_flow import compute_physical_optical_flow, optical_flow_configuration_sha256
from pysteps_param.cli.make_parameters import compute_corlengths
from pysteps_param_bom_cases.archive_events import ROOT, METHOD
from pysteps_param_bom_cases.event_lk import atomic_json
from pysteps_param_bom_cases.research_store import digest
import logging


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',default='syd_2025_archive_v2')
    parser.add_argument('--partial',action='store_true')
    a=parser.parse_args()
    db=get_db(target_db='BOM'); run=db.archive_runs.find_one({'_id':a.run_id})
    if run is None: raise ValueError('Unknown run')
    manifest=run['manifest'];research=db.client[manifest['research_database']]
    q={'metadata.archive.run_id':a.run_id}
    params=list(db.params.find(q)); flows=list(db.oflow_catalogue.find(q))
    cascades=list(db['cascade.files'].find(q))
    checkpoints=list(db.archive_products.find({'run_id':a.run_id}))
    assert len({r['filename'] for r in flows})==len(flows)
    assert len({r['filename'] for r in cascades})==len(cascades)
    assert len({r['metadata']['valid_time'] for r in params})==len(params)
    expected=manifest['event_fields']
    if not a.partial:
        assert run['status']=='complete' and len(params)==len(flows)==expected
        assert db.archive_events.count_documents({'run_id':a.run_id})==107
        assert len(checkpoints)==expected+manifest['support_slots']
    by_name={r['filename']:r for r in flows}
    for p in params:
        md=p['metadata']; ref=md['research_reference'];f=by_name[md['archive']['source_filename']]
        assert f['metadata']['configuration_sha256']==optical_flow_configuration_sha256(METHOD)
        assert f['metadata']['research_reference']==ref
        assert p['advection']['status']==f['status']
        original=research.field_parameters.find_one({'run_id':manifest['field_run'],
            'storm_id':md['archive']['storm_id'],'valid_time':md['valid_time']})
        for k in ('ordinary_mean_db','ordinary_stdev_db','latent_mean_db','latent_stdev_db','rain_fraction','beta_1','beta_2'):
            assert p[k]==original.get(k)
        if f['status']=='data':
            payload=db['oflow.files'].find_one({'_id':f['payload_id']})
            assert payload and digest(payload['metadata'])==digest(f['metadata'])
        else:
            assert 'payload_id' not in f and p['advection']['mean_velocity_m_s'] is None
    # Reconcile all checkpoint references; missing cascades cannot masquerade as dry.
    ids={r['_id'] for r in cascades}
    for r in checkpoints:
        assert r['cascade_id'] is None or r['cascade_id'] in ids
    # Three positions per event (or completed pilot portion) cover both edges and interior.
    samples=[]
    for event in sorted({p['metadata']['archive']['storm_id'] for p in params}):
        rows=sorted([p for p in params if p['metadata']['archive']['storm_id']==event],key=lambda p:p['metadata']['valid_time'])
        samples.extend(rows[i] for i in sorted({0,len(rows)//2,len(rows)-1}))
    max_recompose=max_motion=max_temporal=0.
    direct_motion_checks=temporal_checks=cascade_checks=0
    cascade_by_time={r['metadata']['valid_time']:r for r in cascades}
    transform=get_transformer(manifest['product']['transform'],threshold=manifest['product']['rain_threshold'])
    for p in samples:
        md=p['metadata'];t=md['valid_time'];name=md['archive']['source_filename']
        raw,_=get_rainfield_np(db,name,missing_as_nan=True)
        cr=cascade_by_time.get(t)
        if cr:
            c,_=get_cascade_state(db,cr['filename']);levels=c['cascade_levels']
            assert levels.shape==(7,250,250) and np.isfinite(levels).all()
            restored=(levels*np.asarray(c['stds'])[:,None,None]+np.asarray(c['means'])[:,None,None]).sum(axis=0)
            err=float(np.max(np.abs(restored-transform.transform(raw))))
            assert err<1e-10;max_recompose=max(max_recompose,err);cascade_checks+=1
        f=by_name[name]
        if f['status']=='data':
            v,fmd=get_oflow_state(db,name,expected_method=METHOD,
                expected_configuration_sha256=manifest['configuration_sha256'])
            stats=fmd['advection_statistics']; flat=v.reshape(2,-1)
            np.testing.assert_allclose(stats['mean_velocity_m_s'],flat.mean(1),atol=1e-12)
            np.testing.assert_allclose(stats['stdev_velocity_m_s'],flat.std(1),atol=1e-12)
            assert v.shape==(2,250,250) and np.isfinite(v).all()
            # Fresh native LK extraction independently checks reuse+declustering+interpolation.
            if direct_motion_checks<6:
                pair=research.pairs.find_one({'_id':md['research_reference']['pair_id']})
                prev=pair['inputs']['previous']; before,_=get_rainfield_np(db,prev['filename'],missing_as_nan=True)
                truth=compute_physical_optical_flow(np.stack([transform.transform(before),transform.transform(raw)]),
                    method=METHOD,timestep_seconds=pair['actual_interval_seconds'],pixel_size_metres=1000).velocity_m_s
                err=float(np.max(np.abs(v-truth)));assert err<=0.00500001
                max_motion=max(max_motion,err);direct_motion_checks+=1
            if p['temporal_status'] in ('available','partially_defined'):
                config={**manifest['domain'],**manifest['product'],**manifest['cascade'],'optical_flow_method':METHOD}
                lengths,ac,_=compute_corlengths(db,md,config,300,logging.getLogger('verify'))
                stored=np.array([p['lag_1'],p['lag_2']],dtype=float).T
                np.testing.assert_allclose(stored,ac,rtol=0,atol=1e-12,equal_nan=True)
                if np.isfinite(ac).any(): max_temporal=max(max_temporal,float(np.nanmax(np.abs(stored-ac))))
                if p['corl_zero'] is not None: assert abs(p['corl_zero']-lengths[0])<1e-10
                temporal_checks+=1
    result={'run_id':a.run_id,'partial':a.partial,'parameters':len(params),'flows':dict(Counter(f['status'] for f in flows)),
        'unique_cascades':len(cascades),'checkpoint_slots':len(checkpoints),'sampled_cascades':cascade_checks,
        'fresh_native_motion_checks':direct_motion_checks,'native_temporal_checks':temporal_checks,
        'maximum_recomposition_error_dbr':max_recompose,'maximum_motion_quantisation_error_m_s':max_motion,
        'maximum_temporal_correlation_difference':max_temporal,
        'all_parameter_marginals_and_source_links_checked':True,
        'temporal_status':dict(Counter(p['temporal_status'] for p in params)),
        'defined_correlation_times':sum(p['corl_zero'] is not None for p in params),
        'manifest_sha256':run['manifest_sha256'],
        'verification_source_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
    path=ROOT/a.run_id/('pilot_verification.json' if a.partial else 'verification.json')
    atomic_json(path,result)
    print(result)

if __name__=='__main__':main()
