"""Scientific pair contracts, fixed tracker equivalence and safe publication."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import Mock

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter, shift

from pysteps_param import get_transformer
from pysteps_param.advection.sparse import resolved_lk_parameters, extract_sparse_vectors
from pysteps_param.advection._sparse_lk import extract_sparse_vectors as private_extract
from pysteps_param_bom_cases.event_lk import (
    process_pair, support_at, inventory, FieldCache, research_lk_parameters,
)
from pysteps_param_bom_cases.research_store import ResearchStore, encode_arrays, digest

T = datetime(2025, 1, 1, tzinfo=timezone.utc)
DOMAIN = {"name": "syd_1km", "n_rows": 128, "n_cols": 128,
          "p_size": 1000., "start_x": -63500., "start_y": -63500.}
PRODUCT = {"timestep": 300, "transform": "dbr", "rain_threshold": 1.}


def test_research_feature_limit_preserves_other_core_settings():
    baseline = resolved_lk_parameters()
    selected = research_lk_parameters()
    assert selected["fd_kwargs"]["max_corners"] == 1000
    expected = deepcopy(baseline)
    expected["fd_kwargs"]["max_corners"] = 1000
    assert selected == expected
    assert resolved_lk_parameters() == baseline
    assert digest(selected) != digest(research_lk_parameters(400))
    with pytest.raises(ValueError, match="positive"):
        research_lk_parameters(0)


def record(t, status="data", actual=None):
    return {"filename": t.isoformat(), "status": status,
            "metadata": {"valid_time": t, "source": {
                "source_valid_time": (actual or t).isoformat()}}}


def pair():
    return {"storm_id": "storm", "valid_time": T+timedelta(minutes=5),
            "previous": record(T), "current": record(T+timedelta(minutes=5))}


def loader(a, b):
    transformer = get_transformer("dbr", threshold=1.)
    return lambda r: (a, transformer.transform(a)) if r["metadata"]["valid_time"] == T else (b, transformer.transform(b))


def test_known_translation_and_public_equivalence():
    rng = np.random.default_rng(56)
    a = np.exp(gaussian_filter(rng.normal(size=(128, 128)), 1.1)*3+2)
    b = shift(a, (-2, 3), mode="constant", cval=0, order=1)
    p = pair();p["current"] = record(T+timedelta(minutes=5), actual=T+timedelta(seconds=310))
    settings = resolved_lk_parameters()
    result, arrays = process_pair(p, loader(a, b), DOMAIN, PRODUCT, settings)
    assert result["outcome"] == "vectors" and result["off_grid"]
    assert result["actual_interval_seconds"] == 310
    accepted = ~arrays["outlier_mask"]
    np.testing.assert_allclose(np.median(arrays["uv"][accepted], axis=0), [3, -2], atol=.15)
    np.testing.assert_allclose(arrays["velocity_m_s"], arrays["uv"]*1000/310, rtol=1e-6)
    np.testing.assert_allclose(arrays["projected_xy_m"], arrays["xy"]*1000-63500)
    images = np.stack([get_transformer("dbr", threshold=1).transform(x) for x in (a,b)])
    original = private_extract(images, parameters=settings)
    public = extract_sparse_vectors(images, parameters=settings)
    for name in ("detected_xy", "xy", "uv", "outlier_mask"):
        np.testing.assert_array_equal(getattr(public, name), getattr(original, name))
        np.testing.assert_array_equal(arrays[name], getattr(original, name))


@pytest.mark.parametrize("kind", ["missing", "absent", "invalid_timing"])
def test_unavailable_and_invalid_timing_do_not_read(kind):
    p = pair()
    if kind == "missing":p["previous"]["status"] = "missing"
    elif kind == "absent":p["previous"] = None
    else:p["current"]["metadata"]["source"]["source_valid_time"] = T.isoformat()
    load = Mock(side_effect=AssertionError("Must not read"))
    result, arrays = process_pair(p, load, DOMAIN, PRODUCT, resolved_lk_parameters())
    assert result["outcome"] == ("invalid_timing" if kind == "invalid_timing" else "unavailable")
    assert arrays is None
    load.assert_not_called()


def test_dry_and_masked_inputs():
    a = np.zeros((128, 128));a[40:60,40:60] = np.nan
    p = pair();p["previous"]["status"] = p["current"]["status"] = "zero"
    result, arrays = process_pair(p, loader(a, a), DOMAIN, PRODUCT, resolved_lk_parameters())
    assert result["outcome"] == "no_features"
    assert result["accepted_summary"]["mean_velocity_m_s"] is None
    assert result["valid_fractions"][0] < 1
    assert arrays["uv"].shape == (0,2)
    mask = np.isfinite(a)
    assert support_at(mask, np.array([[10,10], [50,50], [-1,10]])).tolist() == [True,False,False]
    assert support_at(mask, np.array([[39,50], [10,10], [0,0]]), 2).tolist() == [False,True,False]


def test_event_start_predecessor_and_all_slots():
    db = Mock();db.storm_generations.find_one.return_value = {
        "storm_count": 1, "definition": {"domain": "syd_1km", "product": "qpe"}}
    db.storms.find.return_value.sort.return_value = [{"_id":"s", "start":T, "end":T+timedelta(minutes=10)}]
    db.config.find_one.side_effect = [DOMAIN, {**PRODUCT, "product":"qpe"}]
    db.field_catalogue.find.return_value = [record(T-timedelta(minutes=5)),record(T),record(T+timedelta(minutes=10))]
    _,_,_,_,pairs = inventory(db,"generation")
    assert len(pairs) == 3
    assert pairs[0]["previous"]["metadata"]["valid_time"] == T-timedelta(minutes=5)
    assert pairs[-1]["previous"] is None  # Never bridge the absent middle slot.


def test_cache_missing_aware_and_detects_source_change(monkeypatch):
    from pysteps_param_bom_cases import event_lk
    db = Mock();r = record(T);db.field_catalogue.find_one.return_value = deepcopy(r)
    read = Mock(return_value=(np.ones((2,2)),{}));monkeypatch.setattr(event_lk,"get_rainfield_np",read)
    cache = FieldCache(db,PRODUCT);cache(r);cache(r)
    assert read.call_count == 1
    assert read.call_args.kwargs == {"missing_as_nan":True}
    db.field_catalogue.find_one.return_value["updated_at"] = T
    with pytest.raises(ValueError,match="changed"):cache(r)


def test_publish_orders_payload_before_record_and_verifies_resume():
    store = ResearchStore.__new__(ResearchStore);store.db = Mock();store.fs = Mock()
    store.db.runs.find_one.return_value = {"status":"running"}
    store.db.pairs.find_one.return_value = None
    actions=[]
    store.fs.put.side_effect = lambda *a,**kw: actions.append("upload") or "payload"
    store.db.pairs.replace_one.side_effect = lambda *a,**kw: actions.append("publish")
    key={"run_id":"r","storm_id":"s","valid_time":T}
    store.publish(key,{"outcome":"vectors"},{"uv":np.ones((2,2))})
    assert actions == ["upload","publish"]
    published=store.db.pairs.replace_one.call_args.args[1]
    store.db.pairs.find_one.return_value=published
    raw=encode_arrays({"uv":np.ones((2,2))});store.fs.get.return_value.read.return_value=raw
    assert store.read(key)["outcome"] == "vectors"
    store.fs.get.return_value.read.return_value=b"corrupt"
    with pytest.raises(ValueError,match="checksum"):store.read(key)


def test_run_identity_conflict_and_complete_immutability():
    store=ResearchStore.__new__(ResearchStore);store.db=Mock();store.fs=Mock()
    store.db.runs.find_one.return_value={"manifest_sha256":digest({"old":1})}
    with pytest.raises(ValueError,match="conflicts"):store.initialize("r",{"new":1})
    store.db.runs.find_one.return_value={"status":"complete"}
    with pytest.raises(ValueError,match="immutable"):
        store.publish({"run_id":"r"},{"outcome":"no_features"},None)
    store.fs.put.assert_not_called()


@pytest.mark.parametrize("stage", ["no_tracks", "all_rejected", "masked_support"])
def test_stage_outcomes_and_support_do_not_change_lk_filter(monkeypatch, stage):
    from pysteps_param.advection.sparse import SparseVectors
    from pysteps_param_bom_cases import event_lk
    a = np.ones((128,128));a[40:60,40:60] = np.nan
    xy = np.empty((0,2)) if stage == "no_tracks" else np.array([[50.,50.]])
    rejected = np.array([],bool) if stage == "no_tracks" else np.array([stage=="all_rejected"])
    tracked = SparseVectors(np.array([[50.,50.]]),xy,np.zeros_like(xy),rejected)
    monkeypatch.setattr(event_lk,"extract_sparse_vectors",lambda *a,**kw: tracked)
    result, arrays = process_pair(pair(),loader(a,a),DOMAIN,PRODUCT,resolved_lk_parameters())
    assert result["outcome"] == ("vectors" if stage=="masked_support" else stage)
    assert result["counts"]["tracked"] == result["counts"]["accepted"]+result["counts"]["rejected_outlier"]
    if stage == "masked_support":
        assert not arrays["endpoint_valid"][0]
        assert not arrays["base_patch_valid"][0]
        assert not arrays["outlier_mask"][0]  # Separate quality annotation, not a new LK exclusion.


def test_failed_pair_is_retryable_and_failed_upload_is_unpublished():
    store=ResearchStore.__new__(ResearchStore);store.db=Mock();store.fs=Mock()
    store.db.runs.find_one.return_value={"status":"failed"}
    store.db.pairs.find_one.return_value={"outcome":"failed"}
    assert store.read({"run_id":"r"}) is None
    store.fs.put.side_effect=RuntimeError("Upload interrupted")
    with pytest.raises(RuntimeError,match="interrupted"):
        store.publish({"run_id":"r"},{"outcome":"vectors"},{"uv":np.zeros((1,2))})
    store.db.pairs.replace_one.assert_not_called()
