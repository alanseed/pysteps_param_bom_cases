"""Observed support, physical moments and independence from tracking success."""
from unittest.mock import Mock
import numpy as np
import pytest

from pysteps_param_bom_cases.field_parameters import estimate, inputs, clean

PRODUCT={"transform":"dbr","rain_threshold":1.}


def test_missing_pixels_excluded_and_fft_not_invented():
    rain=np.array([[0.,1.],[10.,np.nan]])
    result=estimate(rain,PRODUCT,1.,15.,1.25)
    assert result["valid_fraction"]==.75
    assert result["rain_fraction"]==.5
    assert result["rain_fraction_valid"]==pytest.approx(2/3)
    assert result["mean_rain_mm_h"]==pytest.approx(11/3)
    assert result["wet_mean_rain_mm_h"]==5.5
    assert result["ordinary_mean_db"]==5.
    assert result["ordinary_stdev_db"]==5.
    assert result["nonzero_fit"]["sample_count"]==1
    assert result["latent_mean_db"] is None
    assert result["spectrum"]["status"]=="unavailable_partial_support"
    assert np.isnan(rain[1,1])  # Estimation does not fill or mutate input.


def test_dry_and_constant_fields_have_explicit_unestimated_spectra():
    dry=estimate(np.zeros((64,64)),PRODUCT,1.,15.,1.25)
    assert dry["rain_fraction"]==0 and dry["mean_rain_mm_h"]==0
    assert dry["latent_stdev_db"] is None
    assert dry["spectrum"]["status"]=="insufficient_rain"
    constant=estimate(np.full((64,64),2.),PRODUCT,1.,15.,1.25)
    assert constant["spectrum"]["status"]=="insufficient_variability"


def test_physical_marginals_and_spectral_units():
    rng=np.random.default_rng(19)
    rain=np.exp(rng.normal(1.,.8,(128,128)))
    result=estimate(rain,PRODUCT,1.,15.,1.25)
    assert result["mean_rain_mm_h"]==pytest.approx(rain.mean())
    assert result["stdev_rain_mm_h"]==pytest.approx(rain.std())
    assert result["ordinary_mean_db"]==pytest.approx(np.mean(10*np.log10(rain[rain>=1])))
    assert result["spectrum"]["status"]=="available"
    assert result["nonzero_fit"]["status"]=="available"
    assert result["spectrum"]["model"]["scale_break"]==15.
    assert len(result["spectrum"]["power_db"])==len(result["spectrum"]["frequency_10log10_cycles_per_pixel"])


def test_parameters_include_current_fields_when_tracking_unavailable():
    db=Mock();db.runs.find_one.return_value={"status":"complete","pairs":2}
    db.pairs.find.return_value.sort.return_value=[
        {"storm_id":"a","valid_time":1,"inputs":{"current":{"filename":"one"}}},
        {"storm_id":"a","valid_time":2,"inputs":{"current":{"filename":"two"}}}]
    _,selected=inputs(db,"parent")
    assert [r["source"]["filename"] for r in selected]==["one","two"]
    assert db.pairs.find.call_args.args[0]=={"run_id":"parent"}


@pytest.mark.parametrize("rain",[np.full((3,3),np.nan),np.full((3,3),-1.)])
def test_invalid_available_fields_fail(rain):
    with pytest.raises(ValueError):estimate(rain,PRODUCT,1.,15.,1.25)


def test_nonfinite_estimates_serialize_as_null():
    assert clean({"x":np.array([np.nan,np.inf,1.])})=={"x":[None,None,1.]}
