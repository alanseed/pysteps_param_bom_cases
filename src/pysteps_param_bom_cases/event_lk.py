"""Fixed-LK extraction over a frozen, explicit storm generation."""
import argparse
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
from importlib import metadata
import inspect
import json
import logging
from pathlib import Path
import resource
import subprocess
import time

import numpy as np
from scipy.ndimage import minimum_filter

from pysteps_param import get_db, get_rainfield_np, get_transformer
from pysteps_param.advection.sparse import extract_sparse_vectors, resolved_lk_parameters
from pysteps_param.advection import _sparse_lk

from .research_store import ResearchStore, canonical, digest

UTC = timezone.utc
DEFAULT_GENERATION = "2417c6c4be66438caac46c416efe770a"
DEFAULT_ROOT = Path.home() / "data/alan/psteps_advect_data/runs/event_lk"
CATALOGUE_PROJECTION = {"filename": 1, "status": 1, "reason": 1,
    "payload_id": 1, "shape": 1, "mask_id": 1, "updated_at": 1,
    **{"metadata."+k: 1 for k in (
        "domain", "product", "valid_time", "base_time", "ensemble",
        "ingestion_signature", "units", "threshold_mmph", "valid_fraction",
        "wetted_area_ratio", "storage_policy", "source.path", "source.sha256",
        "source.source_valid_time", "source.offset_seconds")}}


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")
    temporary.replace(path)


def file_identity(record):
    # Retain selected catalogue state; full CRS WKT is frozen once in the domain.
    return None if record is None else {k: v for k, v in record.items() if k != "_id"}


def inventory(db, generation, storm_ids=None):
    pointer = db.storm_generations.find_one({"generation_id": generation})
    if pointer is None:
        raise ValueError("Requested generation is not published")
    storms = list(db.storms.find({"generation_id": generation}).sort("start", 1))
    if len(storms) != pointer["storm_count"]:
        raise ValueError("Incomplete storm generation")
    if storm_ids:
        wanted = set(storm_ids)
        storms = [s for s in storms if str(s["_id"]) in wanted]
        if {str(s["_id"]) for s in storms} != wanted:
            raise ValueError("Unknown storm IDs")
    if not storms:
        raise ValueError("Empty event population")
    definition = pointer["definition"]
    domain = db.config.find_one({"kind": "domain", "name": definition["domain"]},
                              sort=[("inserted_at", -1)])
    product = db.config.find_one({"kind": "analysis", "domain": definition["domain"],
                                 "product": definition["product"]}, sort=[("inserted_at", -1)])
    if domain is None or product is None:
        raise ValueError("Missing grid or product definition")
    # Bureau ingestion contract: arrays have ascending x and y. Generic sources
    # with north-to-south rows need a different explicit grid adapter.
    if domain["name"] != "syd_1km" or product["timestep"] != 300:
        raise ValueError("This adapter requires syd_1km with 300-second slots")
    if domain["p_size"] <= 0:
        raise ValueError("Grid spacing must be positive")
    step = timedelta(seconds=product["timestep"])
    records = {utc(r["metadata"]["valid_time"]): r for r in db.field_catalogue.find({
        "metadata.domain": domain["name"], "metadata.product": product["product"],
        "metadata.base_time": None, "metadata.ensemble": None,
        "metadata.valid_time": {"$gte": min(s["start"] for s in storms)-step,
                                "$lte": max(s["end"] for s in storms)},
    }, CATALOGUE_PROJECTION)}
    pairs = []
    for storm in storms:
        current = utc(storm["start"])
        while current <= utc(storm["end"]):
            pairs.append({"storm_id": str(storm["_id"]), "valid_time": current,
                          "previous": file_identity(records.get(current-step)),
                          "current": file_identity(records.get(current))})
            current += step
    return pointer, storms, domain, product, pairs


def actual_time(record):
    return utc(record["metadata"]["source"]["source_valid_time"])


def support_at(mask, points, radius=0):
    """Observed square base-resolution support; outside-domain support is false.

    This diagnostic is not a guarantee about the full multilevel LK footprint.
    """
    good = minimum_filter(mask.astype(np.uint8), size=2*radius+1,
                          mode="constant", cval=0).astype(bool) if radius else mask
    points = np.rint(points).astype(int)
    inside = ((points[:, 0] >= 0) & (points[:, 0] < mask.shape[1]) &
              (points[:, 1] >= 0) & (points[:, 1] < mask.shape[0]))
    result = np.zeros(len(points), dtype=bool)
    result[inside] = good[points[inside, 1], points[inside, 0]]
    return result


def summaries(velocity):
    if not len(velocity):
        return {"sample_count": 0, "mean_velocity_m_s": None,
                "stdev_velocity_m_s": None, "mean_speed_m_s": None}
    return {"sample_count": len(velocity), "mean_velocity_m_s": velocity.mean(0).tolist(),
            "stdev_velocity_m_s": velocity.std(0).tolist(),
            "mean_speed_m_s": float(np.linalg.norm(velocity, axis=1).mean())}


def process_pair(pair, load, domain, product, settings):
    result = {"schema_version": 1, "inputs": {k: pair[k] for k in ("previous", "current")},
              "nominal_interval_seconds": product["timestep"]}
    previous, current = pair["previous"], pair["current"]
    if any(r is None or r["status"] not in ("data", "zero") for r in (previous, current)):
        return {**result, "outcome": "unavailable", "reason": "missing_or_uncatalogued_input"}, None
    try:
        seconds = (actual_time(current)-actual_time(previous)).total_seconds()
    except (KeyError, ValueError, TypeError):
        return {**result, "outcome": "invalid_timing", "reason": "source_time_absent_or_invalid"}, None
    if seconds <= 0:
        return {**result, "outcome": "invalid_timing", "reason": "non_positive_source_interval"}, None
    result.update(actual_interval_seconds=seconds,
        actual_times={"previous": actual_time(previous), "current": actual_time(current)},
        off_grid=any(actual_time(r) != utc(r["metadata"]["valid_time"]) for r in (previous, current)))
    before, before_image = load(previous)
    after, after_image = load(current)
    shape = (domain["n_rows"], domain["n_cols"])
    if before.shape != shape or after.shape != shape:
        raise ValueError("Input shape differs from frozen domain")
    masks = [np.isfinite(before), np.isfinite(after)]
    if not all(m.any() for m in masks):
        raise ValueError("Available input has no observed pixels")
    sparse = extract_sparse_vectors(np.stack([before_image, after_image]), parameters=settings)
    counts = sparse.counts
    if not counts["detected"]:
        outcome = "no_features"
    elif not counts["tracked"]:
        outcome = "no_tracks"
    elif not counts["accepted"]:
        outcome = "all_rejected"
    else:
        outcome = "vectors"
    velocity = sparse.uv.astype(float) * domain["p_size"] / seconds
    projected_xy = sparse.xy * domain["p_size"] + [domain["start_x"], domain["start_y"]]
    radius = int(settings["lk_kwargs"]["winsize"][0]) // 2
    endpoint_valid = support_at(masks[0], sparse.xy) & support_at(masks[1], sparse.xy+sparse.uv)
    patch_valid = (support_at(masks[0], sparse.xy, radius) &
                   support_at(masks[1], sparse.xy+sparse.uv, radius))
    arrays = {"detected_xy": sparse.detected_xy, "xy": sparse.xy, "uv": sparse.uv,
              "outlier_mask": sparse.outlier_mask, "projected_xy_m": projected_xy,
              "velocity_m_s": velocity, "endpoint_valid": endpoint_valid,
              "base_patch_valid": patch_valid}
    result.update(outcome=outcome, counts=counts,
        units={"detected_xy": "pixels", "xy": "pixels", "uv": "pixels_per_pair",
               "projected_xy_m": "m", "velocity_m_s": "m/s"},
        valid_fractions=[float(m.mean()) for m in masks],
        zero_inputs=[r["status"] == "zero" for r in (previous, current)],
        tracked_summary=summaries(velocity), accepted_summary=summaries(velocity[~sparse.outlier_mask]),
        support_diagnostics={"version": 1, "base_patch_radius_pixels": radius,
                             "valid_endpoints": int(endpoint_valid.sum()),
                             "valid_base_patches": int(patch_valid.sum()),
                             "full_pyramid_support_certified": False},
        coordinate_convention="x_column_y_row; rows_increase_projected_y",
        velocity_components=["projected_x", "projected_y"],
        vector_stage="tracked_pre_decluster_pre_interpolation")
    return result, arrays


class FieldCache:
    """Keep two decoded/transformed inputs; validate the frozen record on load."""
    def __init__(self, db, product):
        self.db, self.cache = db, OrderedDict()
        self.transformer = get_transformer(product["transform"], threshold=product["rain_threshold"])

    def __call__(self, record):
        name = record["filename"]
        live = self.db.field_catalogue.find_one({"filename": name}, CATALOGUE_PROJECTION)
        if digest(file_identity(live)) != digest(record):
            raise ValueError("Source catalogue changed after input snapshot")
        if name not in self.cache:
            rain, _ = get_rainfield_np(self.db, name, missing_as_nan=True)
            self.cache[name] = rain, self.transformer.transform(rain)
        self.cache.move_to_end(name)
        while len(self.cache) > 2:
            self.cache.popitem(last=False)
        return self.cache[name]


def research_lk_parameters(max_corners=1000):
    """Resolve the fixed Bureau research settings without changing core defaults.

    max_corners caps detected features; successful/accepted counts can be lower.
    """
    if max_corners <= 0:
        raise ValueError("max_corners must be positive")
    settings = resolved_lk_parameters()
    settings["fd_kwargs"]["max_corners"] = max_corners
    return settings


def build_manifest(db, pointer, storms, domain, product, pairs, *, max_corners=1000):
    packages = {}
    for package in ("pysteps-param", "pysteps", "numpy", "scipy", "opencv-python", "pymongo"):
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = "not_packaged"
    paths = [Path(__file__), Path(inspect.getfile(ResearchStore)),
             Path(inspect.getfile(_sparse_lk)),
             Path(inspect.getfile(get_rainfield_np)),
             Path(inspect.getfile(get_transformer)),
             Path(inspect.getfile(_sparse_lk.track_features)),
             Path(inspect.getfile(_sparse_lk.morph_opening)),
             Path(inspect.getfile(_sparse_lk.detect_outliers)),
             Path(inspect.getfile(_sparse_lk.feature.get_method("shitomasi")))]
    transformer = get_transformer(product["transform"], threshold=product["rain_threshold"])
    revisions = {}
    for path in (Path(__file__), Path(inspect.getfile(_sparse_lk))):
        try:
            revisions[str(path.parent)] = subprocess.check_output(
                ["git", "-C", str(path.parent), "rev-parse", "HEAD"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            revisions[str(path.parent)] = "unavailable; source hashes are authoritative"
    return {"schema_version": 1, "source_database": db.name,
        "storm_generation": pointer["generation_id"], "storm_config": pointer["storm_config"],
        "storm_definition": pointer["definition"], "storm_ids": [str(s["_id"]) for s in storms],
        "domain": domain, "product": product, "pair_count": len(pairs),
        "input_snapshot_sha256": digest(pairs), "lk_parameters": research_lk_parameters(max_corners),
        "transform": transformer.get_metadata(), "packages": packages,
        "repository_revisions": revisions,
        "software_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "timing": "actual_source_seconds", "row_orientation": "ascending_projected_y",
        "support_policy": "annotations_only_base_patch_v1_no_extra_exclusion"}


def inventory_summary(pairs):
    unavailable = off_grid = zero_inputs = invalid_timing = 0
    for pair in pairs:
        inputs = [pair["previous"], pair["current"]]
        zero_inputs += any(r and r["status"] == "zero" for r in inputs)
        if any(r is None or r["status"] not in ("data", "zero") for r in inputs):
            unavailable += 1
            continue
        try:
            off_grid += any(actual_time(r) != utc(r["metadata"]["valid_time"]) for r in inputs)
            invalid_timing += (actual_time(inputs[1])-actual_time(inputs[0])).total_seconds() <= 0
        except (KeyError, ValueError, TypeError):
            invalid_timing += 1
    return {"unavailable_input_pairs": unavailable, "off_grid_pairs": off_grid,
            "pairs_with_zero_input": zero_inputs, "invalid_timing_pairs": invalid_timing}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", default="BOM")
    parser.add_argument("--research-db", default="BOM_advection")
    parser.add_argument("--generation", default=DEFAULT_GENERATION)
    parser.add_argument("--storm-id", action="append", help="Repeat for a deterministic pilot subset")
    parser.add_argument("--run-id", help="Required for writes; use same ID and unchanged inputs to resume")
    parser.add_argument("--dry-run", action="store_true", help="Read-only inventory; no files or database writes")
    parser.add_argument("--max-corners", type=int, default=1000,
                        help="Maximum detected LK features per input pair (default: 1000)")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    if not args.dry_run and (not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in (".", "..")):
        parser.error("Writes require a simple --run-id")
    if args.source_db == args.research_db:
        parser.error("Research and source databases must differ")
    db = get_db(target_db=args.source_db)
    if db.client.address[0] not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Expected a local source database")
    pointer, storms, domain, product, pairs = inventory(db, args.generation, args.storm_id)
    manifest = build_manifest(db, pointer, storms, domain, product, pairs, max_corners=args.max_corners)
    overview = {"storms": len(storms), "pairs": len(pairs),
                "manifest_sha256": digest(manifest), "storm_ids": manifest["storm_ids"],
                "input_conditions": inventory_summary(pairs)}
    if args.dry_run:
        print(canonical(overview))
        return 0
    run_dir = args.report_root.expanduser() / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "writer.lock").open("a") as lock:
        # Database-wide lock also excludes a second writer using another report root.
        lock_root = DEFAULT_ROOT.expanduser()
        lock_root.mkdir(parents=True, exist_ok=True)
        with (lock_root / (digest([args.research_db, args.run_id])+".lock")).open("a") as global_lock:
            fcntl.flock(global_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            store = ResearchStore(db.client[args.research_db])
            store.initialize(args.run_id, manifest)
            existing = store.db.runs.find_one({"_id": args.run_id})
            if existing["status"] == "complete":
                # Verify references but preserve the completed scientific record.
                for pair in pairs:
                    key = {"run_id": args.run_id, "storm_id": pair["storm_id"],
                           "valid_time": pair["valid_time"]}
                    if store.read(key) is None:
                        raise ValueError("Complete run has missing pair records")
                print(canonical({k: v for k, v in existing.items() if k not in ("manifest", "_id")}))
                return 0
            atomic_json(run_dir/"manifest.json", manifest)
            atomic_json(run_dir/"input_snapshot.json", pairs)
            cache = FieldCache(db, product)
            total = Counter()
            started = time.monotonic()
            for storm in storms:
                sid = str(storm["_id"])
                counts = Counter()
                event_pairs = [p for p in pairs if p["storm_id"] == sid]
                for pair in event_pairs:
                    key = {"run_id": args.run_id, "storm_id": sid, "valid_time": pair["valid_time"]}
                    record = store.read(key)
                    if record is None:
                        try:
                            record, arrays = process_pair(pair, cache, domain, product, manifest["lk_parameters"])
                        except Exception as exc:
                            logging.exception("Pair failed: %s", key)
                            record, arrays = {"outcome": "failed", "error": f"{type(exc).__name__}: {exc}"}, None
                        store.publish(key, record, arrays)
                    counts[record["outcome"]] += 1
                total.update(counts)
                event = {"run_id": args.run_id, "storm_id": sid,
                         "planned_pairs": len(event_pairs), "outcomes": dict(counts),
                         "status": "failed" if counts["failed"] else "complete"}
                store.db.events.update_one({"run_id": args.run_id, "storm_id": sid}, {"$set": event}, upsert=True)
                print(canonical(event), flush=True)
                atomic_json(run_dir/(sid+".json"), event)
            summary = {**overview, "run_id": args.run_id, "outcomes": dict(total),
                       "elapsed_seconds": time.monotonic()-started,
                       "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                       "orphan_payloads": store.orphan_count(args.run_id),
                       "status": "failed" if total["failed"] else "complete"}
            assert sum(total.values()) == len(pairs)
            # Reject changes to even skipped/missing inputs during processing.
            _, _, _, _, live_pairs = inventory(db, args.generation, args.storm_id)
            if digest(live_pairs) != manifest["input_snapshot_sha256"]:
                raise ValueError("Input population changed during extraction; run not completed")
            store.db.runs.update_one({"_id": args.run_id}, {"$set": summary})
            atomic_json(run_dir/"summary.json", summary)
            print(canonical(summary), flush=True)
    return 1 if total["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
