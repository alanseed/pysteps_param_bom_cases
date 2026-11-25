"""Restartable native archive products for the frozen 107-event Sydney population."""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
import fcntl
from hashlib import sha256
from importlib import metadata
from io import BytesIO
import inspect
import json
import logging
import multiprocessing
from pathlib import Path
import time

import gridfs
import numpy as np
from pysteps import utils
from pysteps.utils.cleansing import decluster
from pysteps_param import get_db, make_nc_name, get_rainfield_np
from pysteps_param.field_artifacts import CascadeGenerator, TransformedField
from pysteps_param.db.core.state_io import (
    write_cascade_state, get_cascade_state, write_oflow_state,
    write_missing_oflow_state, get_oflow_state,
)
from pysteps_param.db.core.optical_flow_storage import velocity_statistics
from pysteps_param.optical_flow import (
    LK_1000_PARAMETER_SET, lk_1000_parameters, optical_flow_configuration_sha256,
    velocity_m_s_to_pixels_per_timestep,
)
from pysteps_param.cli.make_parameters import _parameter_advection_summary
from pysteps_param.rain_stats_compute import (
    lagr_auto_cor, correlation_length, CORRELATION_TIME_DEFINITION,
)
from pysteps_param.cascade_utils import calculate_wavelengths
from pysteps_param.steps_params import PARAMETER_SCHEMA_VERSION

from .event_lk import (
    inventory, FieldCache, actual_time, utc, file_identity, CATALOGUE_PROJECTION, atomic_json,
)
from .field_parameters import clean
from .research_store import digest

ROOT = Path.home()/"data/alan/psteps_advect_data/runs/archive_events"
METHOD = LK_1000_PARAMETER_SET
LK_RUN = "syd_2025_fixed_lk_v1"
FIELDS_RUN = "syd_2025_fields_v1"
E1_RUN = "syd_2025_characterisation_v1"


def dense_from_sparse(xy, velocity, shape, parameters):
    """Apply native LK declustering and IDW to accepted observations, in m/s.

    An empty accepted set is unavailable, not evidence of zero motion. Genuine
    zero vectors remain zero. Sparse observations are not rerun or reclassified.
    """
    xy, velocity = np.asarray(xy, float), np.asarray(velocity, float)
    if xy.shape != velocity.shape or xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError("Expected matched (n, 2) locations and velocities")
    if not len(xy):
        return None
    if not np.isfinite(xy).all() or not np.isfinite(velocity).all():
        raise ValueError("Nonfinite accepted LK observations")
    if parameters["decl_scale"] > 1:
        xy, velocity = decluster(xy, velocity, parameters["decl_scale"], 1)
    if not len(xy):
        return None
    field = utils.get_method(parameters["interp_method"])(
        xy, velocity, np.arange(shape[1]), np.arange(shape[0]),
        **parameters["interp_kwargs"])
    if field.shape != (2, *shape) or not np.isfinite(field).all():
        raise ValueError("Invalid dense interpolation")
    return field


def temporal_parameters(history, velocity, pixel_size, seconds, levels):
    """Native Lagrangian AR(2) diagnostics on three complete regular-cadence fields."""
    empty = {"status": "unavailable", "corl_zero": None, "lag_1": [None]*levels,
             "lag_2": [None]*levels}
    if velocity is None:
        return {**empty, "reason": "missing_motion"}
    if len(history) != 3 or any(v is None for v in history):
        return {**empty, "reason": "missing_or_partial_cascade_history"}
    displacement = velocity_m_s_to_pixels_per_timestep(
        velocity, timestep_seconds=seconds, pixel_size_metres=pixel_size)
    ac = lagr_auto_cor(np.asarray(history), displacement)
    lengths = [correlation_length(a, b, seconds/60) for a, b in ac]
    return clean({"status": "available" if np.isfinite(ac).all() else "partially_defined",
                  "reason": None, "corl_zero": lengths[0],
                  "lag_1": ac[:, 0], "lag_2": ac[:, 1]})


def parameter_document(rain_parameters, temporal, md, advection, cascade, pixel_size, shape):
    """Adapt existing missing-aware marginals to the native forecast parameter schema."""
    p = rain_parameters
    levels = cascade["n_cascade_levels"]
    return clean({"metadata": {**md, "cascade_name": cascade["name"],
                     "correlation_time_definition": CORRELATION_TIME_DEFINITION},
        "parameter_schema_version": PARAMETER_SCHEMA_VERSION, "advection": advection,
        **{k: p.get(k) for k in ("ordinary_mean_db", "ordinary_stdev_db", "latent_mean_db",
                               "latent_stdev_db", "rain_fraction", "beta_1", "beta_2")},
        "nonzero_fit": {"distribution": "normal", "method": "lower_truncated_maximum_likelihood",
            "rain_threshold": 1.0, "rain_threshold_units": "mm/h", "transformed_threshold": 0.0,
            "lower_limit_dbr": p["nonzero_fit"]["lower_limit_dbr"]},
        "scale_break": cascade["scale_break_km"],
        **{k: temporal[k] for k in ("corl_zero", "lag_1", "lag_2")},
        "temporal_status": temporal["status"], "temporal_reason": temporal.get("reason"),
        "spectrum_status": p["spectrum"]["status"],
        "wave_lengths": calculate_wavelengths(levels, max(shape), 1000/pixel_size).tolist()})


def build_plan(db, research):
    parent = research.runs.find_one({"_id": LK_RUN, "status": "complete"})
    if parent is None:
        raise ValueError("Completed fixed LK archive required")
    pointer, storms, domain, product, pairs = inventory(db, parent["manifest"]["storm_generation"])
    if len(storms) != 107 or len(pairs) != 23134:
        raise ValueError("Unexpected event population")
    if digest(parent["manifest"]["lk_parameters"]) != digest(lk_1000_parameters()):
        raise ValueError("Registered baseline differs from frozen research LK parameters")
    for name, run_id in (("field_parameter_runs", FIELDS_RUN), ("characterisation_runs", E1_RUN)):
        if research[name].find_one({"_id": run_id, "status": "complete"}) is None:
            raise ValueError(f"Completed {run_id} required")
    cascade = db.config.find_one({"kind": "cascade", "name": "standard"}, sort=[("inserted_at", -1)])
    model = db.config.find_one({"kind": "steps_nowcast", "domain": domain["name"],
                               "steps.input_analysis": product["product"]}, sort=[("inserted_at", -1)])
    if cascade is None or model is None or model["steps"]["ar_order"] != 2:
        raise ValueError("Configured AR(2) model and standard cascade required")
    snapshots = []
    for storm in storms:
        start, end = utc(storm["start"]), utc(storm["end"])
        # Two preceding cascades support both AR lags at the first event time.
        for t in (start-timedelta(seconds=600), start-timedelta(seconds=300)):
            name = make_nc_name(domain["name"], product["product"], t, None, None)
            record = db.field_catalogue.find_one({"filename": name}, CATALOGUE_PROJECTION)
            snapshots.append({"storm_id": str(storm["_id"]), "valid_time": t,
                              "current": file_identity(record), "support_only": True})
    snapshots += [{"storm_id": p["storm_id"], "valid_time": p["valid_time"],
                   "current": p["current"], "support_only": False} for p in pairs]
    manifest = {"schema_version": 1, "database": db.name, "research_database": research.name,
        "lk_run": LK_RUN, "lk_manifest_sha256": parent["manifest_sha256"],
        "field_run": FIELDS_RUN, "e1_run": E1_RUN,
        "field_manifest_sha256": research.field_parameter_runs.find_one({"_id": FIELDS_RUN})["manifest_sha256"],
        "e1_manifest_sha256": research.characterisation_runs.find_one({"_id": E1_RUN})["manifest_sha256"],
        "storm_generation": pointer["generation_id"], "storm_ids": [str(s["_id"]) for s in storms],
        "domain": domain, "product": {k:v for k,v in product.items() if k != "optical_flow_method"},
        "cascade": cascade, "model": model, "method": METHOD,
        "configuration_sha256": optical_flow_configuration_sha256(METHOD),
        "lk_parameters": lk_1000_parameters(), "input_snapshot_sha256": digest(snapshots),
        "event_fields": len(pairs), "support_slots": len(snapshots)-len(pairs),
        "cascade_missing_policy": "require_fully_observed_grid_no_infill",
        "motion_source": "accepted_frozen_sparse_LK_actual_source_interval",
        "temporal_advection": "current_velocity_m_s_times_nominal_lag_seconds",
        "temporal_boundary_policy": "native_lagr_auto_cor_zero_for_outside_advected_domain",
        "time_zone": "UTC",
        "software_sha256": {str(Path(inspect.getfile(obj))): sha256(Path(inspect.getfile(obj)).read_bytes()).hexdigest()
            for obj in (build_plan, CascadeGenerator, write_oflow_state, lagr_auto_cor,
                        optical_flow_configuration_sha256, _parameter_advection_summary)},
        "packages": {name: metadata.version(name) for name in ("pysteps-param", "pysteps", "numpy", "scipy", "pymongo")}}
    return manifest, snapshots


def _check_owner(record, run_id):
    if record and record.get("metadata", {}).get("archive", {}).get("run_id") != run_id:
        raise ValueError("Refusing to overwrite an unrelated archive product")


def process_event(task):
    run_id, manifest, slots = task
    db = get_db(target_db=manifest["database"])
    research = db.client[manifest["research_database"]]
    product, domain, cascade = manifest["product"], manifest["domain"], manifest["cascade"]
    cache = FieldCache(db, product)
    shape = (domain["n_rows"], domain["n_cols"])
    generator = CascadeGenerator(cascade["name"], shape, cascade["n_cascade_levels"])
    fs = gridfs.GridFS(research, collection="sparse_oflow")
    history = OrderedDict()
    totals = Counter()
    for item in sorted(slots, key=lambda r: r["valid_time"]):
        t = utc(item["valid_time"]); source = item["current"]
        name = make_nc_name(domain["name"], product["product"], t, None, None)
        key = {"run_id": run_id, "storm_id": item["storm_id"], "filename": name}
        done = db.archive_products.find_one(key)
        if source is not None:
            live = db.field_catalogue.find_one({"filename": name}, CATALOGUE_PROJECTION)
            if digest(file_identity(live)) != digest(source):
                raise ValueError("Frozen input changed")
        elif db.field_catalogue.find_one({"filename": name}) is not None:
            raise ValueError("Previously uncatalogued support input appeared")
        if done:
            if done["input_sha256"] != digest(source):
                raise ValueError("Checkpoint source changed")
            if done.get("cascade_id"):
                c, _ = get_cascade_state(db, done["cascade_filename"])
                if not c:
                    raise ValueError("Completed cascade cannot be read")
                history[t] = c["cascade_levels"]
            else:
                history[t] = None
            totals.update(done["outcomes"])
            while len(history)>3: history.popitem(last=False)
            continue
        rain = image = md = None
        if source is not None and source["status"] in ("data", "zero"):
            rain, image = cache(source)
            md = db.field_catalogue.find_one({"filename": name}, {"metadata": 1})["metadata"]
            if rain.shape != shape:
                raise ValueError("Unexpected rainfall grid")
        archive_md = {"run_id": run_id, "storm_id": item["storm_id"],
                      "storm_generation": manifest["storm_generation"],
                      "support_only": item["support_only"], "input_sha256": digest(source),
                      "source_filename": name, "source_catalogue_payload_id": source.get("payload_id") if source else None,
                      "missing_policy": manifest["cascade_missing_policy"]}
        common = {"domain": domain["name"], "product": product["product"], "valid_time": t,
                  "base_time": None, "ensemble": None, "time_delta": product["timestep"],
                  **cache.transformer.get_metadata(), "archive": archive_md}
        cascade_id = cascade_name = None
        outcomes = Counter()
        if rain is not None and np.isfinite(rain).all():
            field = TransformedField(name, image, md, cache.transformer.get_metadata(), rain)
            c, cascade_name, cmd = generator.generate(field, product["timestep"])
            if not np.isfinite(c["cascade_levels"]).all():
                raise ValueError("Nonfinite cascade")
            existing_cascade = db['cascade.files'].find_one({"filename": cascade_name})
            _check_owner(existing_cascade, run_id)
            if existing_cascade:
                if existing_cascade["metadata"]["archive"]["input_sha256"] != digest(source):
                    raise ValueError("Shared cascade source changed")
                cascade_id = existing_cascade["_id"]
            else:
                cascade_id = write_cascade_state(db, c, cascade_name, {**cmd, "archive": archive_md})
            history[t] = c["cascade_levels"]
            outcomes["cascade_data"] += 1
        else:
            history[t] = None
            outcomes["cascade_unavailable"] += 1
        velocity = None
        if not item["support_only"]:
            pair = research.pairs.find_one({"run_id": LK_RUN, "storm_id": item["storm_id"], "valid_time": t})
            if pair is None or digest(pair["inputs"]["current"]) != digest(source):
                raise ValueError("Frozen LK source mismatch")
            # Verify predecessor identity even though the already-estimated sparse observations are reused.
            previous = pair["inputs"]["previous"]
            previous_name = make_nc_name(domain["name"], product["product"], t-timedelta(seconds=300), None, None)
            live = db.field_catalogue.find_one({"filename": previous_name}, CATALOGUE_PROJECTION)
            if digest(file_identity(live)) != digest(previous):
                raise ValueError("Frozen LK predecessor changed")
            reference = {"database": research.name, "lk_run": LK_RUN, "pair_id": pair["_id"],
                         "payload_sha256": pair.get("payload_sha256"), "field_run": FIELDS_RUN,
                         "characterisation_run": E1_RUN}
            fmd = {**common, "research_reference": reference, "units": "m s-1",
                   "optical_flow_method": METHOD, "optical_flow_parameter_set": METHOD,
                   "configuration_sha256": manifest["configuration_sha256"],
                   "pixel_size_metres": domain["p_size"], "quality": "good", "quality_reasons": [],
                   "coordinate_convention": "x_column_y_row; rows_increase_projected_y",
                   "actual_interval_seconds": pair.get("actual_interval_seconds"),
                   "actual_times": pair.get("actual_times"), "sparse_counts": pair.get("counts"),
                   "support_diagnostics": pair.get("support_diagnostics")}
            if pair["outcome"] == "vectors":
                raw = fs.get(pair["payload_id"]).read()
                if sha256(raw).hexdigest() != pair["payload_sha256"]:
                    raise ValueError("Sparse payload checksum mismatch")
                with np.load(BytesIO(raw), allow_pickle=False) as a:
                    accepted = ~a["outlier_mask"]
                    velocity = dense_from_sparse(a["xy"][accepted], a["velocity_m_s"][accepted],
                                                 shape, manifest["lk_parameters"])
            _check_owner(db.oflow_catalogue.find_one({"filename": name}), run_id)
            if velocity is None:
                reason = pair.get("reason", pair["outcome"])
                fmd.update(quality="unavailable", quality_reasons=[reason])
                write_missing_oflow_state(db, name, fmd, reason=reason)
                outcomes["flow_missing"] += 1
            else:
                write_oflow_state(db, velocity, name, fmd)
                # Temporal parameters use exactly the packed/decoded motion consumed by forecasts.
                velocity, _ = get_oflow_state(db, name, expected_method=METHOD,
                    expected_configuration_sha256=manifest["configuration_sha256"])
                outcomes["flow_data"] += 1
            field_params = research.field_parameters.find_one({"run_id": FIELDS_RUN,
                "storm_id": item["storm_id"], "valid_time": t})
            if field_params is None or field_params["status"] != "data":
                raise ValueError("Expected completed per-field rainfall parameters")
            temporal = temporal_parameters([history.get(t-timedelta(seconds=300*i)) for i in (2,1,0)],
                                           velocity, domain["p_size"], 300, cascade["n_cascade_levels"])
            pmd = {**common, "research_reference": reference}
            pdoc = parameter_document(field_params, temporal, pmd, _parameter_advection_summary(db, name),
                                      cascade, domain["p_size"], shape)
            pkey = {"metadata.domain": domain["name"], "metadata.product": product["product"],
                    "metadata.valid_time": t, "metadata.base_time": None, "metadata.ensemble": None,
                    "metadata.cascade_name": cascade["name"], "metadata.parameter_role": {"$ne": "generation"}}
            _check_owner(db.params.find_one(pkey), run_id)
            db.params.replace_one(pkey, pdoc, upsert=True)
            outcomes["params"] += 1
            outcomes["temporal_"+temporal["status"]] += 1
        db.archive_products.replace_one(key, {**key, "storm_id": item["storm_id"], "valid_time": t,
            "input_sha256": digest(source), "support_only": item["support_only"],
            "cascade_id": cascade_id, "cascade_filename": cascade_name, "outcomes": dict(outcomes)}, upsert=True)
        totals.update(outcomes)
        while len(history)>3: history.popitem(last=False)
    return {"storm_id": slots[0]["storm_id"], "totals": dict(totals)}


def process_group(tasks):
    return [process_event(task) for task in tasks]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="syd_2025_archive_v2")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--storm-id", action="append", help="Execute selected events within the unchanged full-run manifest")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if Path(args.run_id).name != args.run_id or args.run_id in (".", "..") or args.workers<1:
        parser.error("Simple run ID and positive worker count required")
    logging.basicConfig(level=logging.WARNING)
    db = get_db(target_db="BOM"); research = db.client.BOM_advection
    if db.client.address[0] not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("This runner is restricted to the local BOM database")
    manifest, snapshots = build_plan(db, research)
    groups = {}
    for item in snapshots: groups.setdefault(item["storm_id"], []).append(item)
    if args.storm_id and not set(args.storm_id).issubset(groups):
        parser.error("Unknown storm ID")
    selected = args.storm_id or list(groups)
    identity = digest(manifest)
    print(json.dumps({"run_id": args.run_id, "manifest_sha256": identity,
        "events": len(groups), "executing_events": len(selected), "event_fields": manifest["event_fields"],
        "support_slots": manifest["support_slots"], "method": METHOD}), flush=True)
    if args.dry_run: return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT/"BOM_archive.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        existing = db.archive_runs.find_one({"_id": args.run_id})
        if existing and existing["manifest_sha256"] != identity:
            raise ValueError("Run inputs/configuration/software changed; use a new run ID")
        db.archive_products.create_index([("run_id",1),("storm_id",1),("filename",1)], unique=True)
        db.archive_runs.update_one({"_id": args.run_id}, {"$setOnInsert": {
            "manifest": manifest, "manifest_sha256": identity, "status": "running"}}, upsert=True)
        out = ROOT/args.run_id; out.mkdir(exist_ok=True)
        atomic_json(out/"manifest.json", manifest); atomic_json(out/"input_snapshot.json", snapshots)
        software_dir = out/"software"; software_dir.mkdir(exist_ok=True)
        for source_path, expected_hash in manifest["software_sha256"].items():
            raw = Path(source_path).read_bytes()
            if sha256(raw).hexdigest() != expected_hash:
                raise ValueError("Software changed during planning")
            (software_dir/(expected_hash+"_"+Path(source_path).name)).write_bytes(raw)
        # Change only the selected input's method. All other native defaults remain unchanged.
        db.config.update_one({"_id": manifest["product"]["_id"]}, {"$set": {"optical_flow_method": METHOD}})
        started=time.monotonic()
        # Adjacent storms can share a supporting cascade. Serialize each connected
        # group, while independent groups run concurrently, to avoid GridFS races.
        batches = []
        occupied = set()
        for k, slots in groups.items():
            times = {r["valid_time"] for r in slots}
            if not times & occupied:
                batches.append([])
                occupied = set()
            if k in selected:
                batches[-1].append((args.run_id, manifest, slots))
            occupied.update(times)
        batches = [b for b in batches if b]
        completed = 0
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            for results in pool.map(process_group, batches):
                for result in results:
                    completed += 1
                    db.archive_events.replace_one({"run_id": args.run_id, "storm_id": result["storm_id"]},
                        {"run_id": args.run_id, **result}, upsert=True)
                    print(json.dumps({"completed_in_pass": completed, **result}), flush=True)
        all_events=list(db.archive_events.find({"run_id": args.run_id}))
        totals=Counter()
        for r in all_events: totals.update(r["totals"])
        summary={"run_id": args.run_id, "status": "complete" if len(all_events)==len(groups) else "partial",
                 "events": len(all_events), "totals": dict(totals), "seconds_this_pass": time.monotonic()-started}
        atomic_json(out/"summary.json", summary)
        db.archive_runs.update_one({"_id": args.run_id}, {"$set": summary})
        print(json.dumps(summary), flush=True)
    return 0
