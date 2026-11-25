import numpy as np
import pytest
from pysteps_param_bom_cases.archive_events import dense_from_sparse, temporal_parameters
from pysteps_param.optical_flow import lk_1000_parameters


def test_uniform_sparse_motion_and_true_zero():
    xy = np.array([[2.,2.],[14.,2.],[2.,14.],[14.,14.]])
    for uv in ([3.,-2.], [0.,0.]):
        result = dense_from_sparse(xy, np.tile(uv, (4,1)), (20,20), lk_1000_parameters())
        np.testing.assert_allclose(result, np.broadcast_to(np.array(uv)[:,None,None], (2,20,20)), atol=1e-12)


def test_no_features_not_zero_motion():
    assert dense_from_sparse(np.empty((0,2)), np.empty((0,2)), (20,20), lk_1000_parameters()) is None


def test_nonfinite_sparse_rejected():
    with pytest.raises(ValueError, match='Nonfinite'):
        dense_from_sparse([[2.,2.]], [[np.nan,0.]], (20,20), lk_1000_parameters())


def test_temporal_missingness():
    assert temporal_parameters([None]*3, None, 1000,300,7)['reason']=='missing_motion'
    assert temporal_parameters([None]*3, np.zeros((2,20,20)),1000,300,7)['reason']=='missing_or_partial_cascade_history'


def test_temporal_perfect_stationary_history():
    rng=np.random.default_rng(40)
    field=rng.normal(size=(7,20,20))
    p=temporal_parameters([field]*3,np.zeros((2,20,20)),1000,300,7)
    np.testing.assert_allclose(p['lag_1'],1)
    np.testing.assert_allclose(p['lag_2'],1)
