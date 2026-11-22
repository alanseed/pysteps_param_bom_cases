"""Missing-aware, non-Lagrangian rainfall descriptors for frozen storm fields."""
import argparse
from collections import Counter
import fcntl
import hashlib
import inspect
from importlib import metadata as package_metadata
from pathlib import Path
import time

import numpy as np
from pysteps_param import get_db, get_rainfield_np, get_transformer
from pysteps_param.rain_stats_compute import compute_field_stats, power_spectrum_1D

from .event_lk import CATALOGUE_PROJECTION, atomic_json, file_identity
from .research_store import digest, canonical

ROOT = Path.home()/"data/alan/psteps_advect_data/runs/field_parameters"


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple,np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, (float,np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def estimate(rain, product, pixel_km, scale_break_km, lower_limit_dbr):
    """Estimate marginals on observed pixels; FFT only on fully observed grids.

    Rain fractions retain the configured whole-domain denominator. A second
    fraction over observed support makes partial-field interpretation explicit.
    Missing support is never filled with dry rainfall for a spectral estimate.
    """
    rain=np.asarray(rain,float)
    valid=np.isfinite(rain)
    if not valid.any() or np.any(rain[valid]<0):
        raise ValueError("Available rainfall must have nonnegative observed support")
    transform=get_transformer(product["transform"],threshold=product["rain_threshold"])
    image=transform.transform(rain)
    metadata=transform.get_metadata()
    if lower_limit_dbr < metadata["threshold"]:
        raise ValueError("Latent fitting cutoff is below rain threshold")
    stats=compute_field_stats(image,metadata,fit_truncated=True,lower_truncation=lower_limit_dbr)
    wet=valid & (rain>=product["rain_threshold"])
    sample=rain[valid]
    result={"status":"data", "valid_fraction":float(valid.mean()),
        "rain_fraction":float(wet.sum()/rain.size),
        "rain_fraction_valid":float(wet.sum()/valid.sum()),
        "ordinary_mean_db":stats["ordinary_mean"],"ordinary_stdev_db":stats["ordinary_stdev"],
        "latent_mean_db":stats["latent_mean"],"latent_stdev_db":stats["latent_stdev"],
        "mean_rain_mm_h":float(sample.mean()),"stdev_rain_mm_h":float(sample.std()),
        "max_rain_mm_h":float(sample.max()),
        "wet_mean_rain_mm_h":float(rain[wet].mean()) if wet.any() else None,
        "wet_quantiles_mm_h":np.quantile(rain[wet],[.5,.9,.99]).tolist() if wet.any() else None,
        "nonzero_fit":{"family":"normal","method":"lower_truncated_maximum_likelihood",
            "lower_limit_dbr":lower_limit_dbr,"sample_count":int(np.count_nonzero(valid & (image>=lower_limit_dbr))),
            "status":"available" if np.isfinite(stats["latent_mean"]) and np.isfinite(stats["latent_stdev"]) else "undefined"},
        "transform":metadata,"scale_break_km":scale_break_km,
        "beta_1":None,"beta_2":None}
    if not valid.all():
        result["spectrum"]={"status":"unavailable_partial_support"}
    elif result["rain_fraction"] < .01:
        result["spectrum"]={"status":"insufficient_rain"}
    else:
        spectrum,model=power_spectrum_1D(image,scale_break_km/pixel_km)
        if spectrum is None or model is None:
            result["spectrum"]={"status":"insufficient_variability"}
        else:
            if not all(np.isfinite(model[k]) for k in ("beta_1","beta_2")):
                raise ValueError("Nonfinite spectral fit")
            result.update(beta_1=model["beta_1"],beta_2=model["beta_2"])
            result["spectrum"]={"status":"available","model":model,
                "frequency_10log10_cycles_per_pixel":spectrum.psd_bins.values.tolist(),
                "power_db":spectrum.psd.values.tolist(),
                "definition":"radial_mean_unscaled_fft_power_of_standardized_dbr",
                "slope_convention":"signed_power_vs_frequency; beta_1 below break frequency, beta_2 above"}
    return clean(result)


def inputs(research, lk_run):
    run=research.runs.find_one({"_id":lk_run})
    if run is None or run["status"]!="complete":
        raise ValueError("A completed source LK inventory is required")
    # Select current fields regardless of the outcome of tracking their predecessor.
    rows=list(research.pairs.find({"run_id":lk_run},
        {"storm_id":1,"valid_time":1,"inputs.current":1}).sort("valid_time",1))
    if len(rows)!=run["pairs"]:
        raise ValueError("Source run pair inventory is incomplete")
    return run,[{"storm_id":r["storm_id"],"valid_time":r["valid_time"],
                  "source":r["inputs"]["current"]} for r in rows]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lk-run",default="syd_2025_fixed_lk_v1")
    parser.add_argument("--run-id",default="syd_2025_fields_v1")
    parser.add_argument("--research-db",default="BOM_advection")
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(argv)
    if Path(args.run_id).name!=args.run_id or args.run_id in (".",".."):
        parser.error("Use a simple run ID")
    research=get_db(target_db=args.research_db)
    if research.client.address[0] not in ("localhost","127.0.0.1","::1"):
        raise ValueError("Expected local database")
    parent,selected=inputs(research,args.lk_run)
    source=research.client[parent["manifest"]["source_database"]]
    if source.name==research.name:
        raise ValueError("Research and source databases must differ")
    domain=parent["manifest"]["domain"];product=parent["manifest"]["product"]
    model=source.config.find_one({"kind":"steps_nowcast","domain":domain["name"],
        "steps.input_analysis":product["product"]},sort=[("inserted_at",-1)])
    if model is None:raise ValueError("No configured rainfall estimator model")
    latent=model["steps"]["latent_distribution"]
    if latent["family"]!="normal":raise ValueError("Only the configured Normal fit is supported")
    cascade=source.config.find_one({"kind":"cascade","name":model["steps"]["cascade_name"]},
                                  sort=[("inserted_at",-1)])
    cutoff=float(latent["lower_limit_dbr"]);scale=float(cascade["scale_break_km"])
    paths=[Path(__file__),Path(inspect.getfile(compute_field_stats)),Path(inspect.getfile(get_rainfield_np)),
           Path(inspect.getfile(get_transformer))]
    packages={}
    for name in ("pysteps-param","pysteps","numpy","scipy","pymongo"):
        packages[name]=package_metadata.version(name)
    manifest={"schema_version":1,"source_lk_run":args.lk_run,
        "source_lk_manifest_sha256":parent["manifest_sha256"],
        "storm_generation":parent["manifest"]["storm_generation"],
        "input_snapshot_sha256":digest(selected),"field_count":len(selected),
        "cascade":cascade,"latent_distribution":latent,"domain":domain,"product":product,
        "spectrum_minimum_domain_war":.01,"spectrum_missing_policy":"require_complete_grid",
        "packages":packages,
        "software_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    if args.dry_run:
        print(canonical({"fields":len(selected),"manifest_sha256":digest(manifest)}));return 0
    out=ROOT/args.run_id;out.mkdir(parents=True,exist_ok=True)
    with (ROOT/(digest([research.name,args.run_id])+".lock")).open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        runs=research.field_parameter_runs;collection=research.field_parameters
        collection.create_index([("run_id",1),("storm_id",1),("valid_time",1)],unique=True)
        existing=runs.find_one({"_id":args.run_id})
        if existing and existing["manifest_sha256"]!=digest(manifest):
            raise ValueError("Field run inputs or estimator changed; use a new run ID")
        runs.update_one({"_id":args.run_id},{"$setOnInsert":{
            "manifest":manifest,"manifest_sha256":digest(manifest),"status":"running"}},upsert=True)
        atomic_json(out/"manifest.json",manifest);atomic_json(out/"input_snapshot.json",selected)
        totals=Counter();spectrum_totals=Counter();fit_totals=Counter();started=time.monotonic()
        for i,item in enumerate(selected):
            key={"run_id":args.run_id,"storm_id":item["storm_id"],"valid_time":item["valid_time"]}
            record=item["source"]
            if record is not None:
                live=source.field_catalogue.find_one({"filename":record["filename"]},CATALOGUE_PROJECTION)
                if digest(file_identity(live))!=digest(record):raise ValueError("Source field changed")
            doc=collection.find_one(key)
            if doc is None or doc["status"]=="failed":
                if existing and existing["status"]=="complete":raise ValueError("Completed run is inconsistent")
                try:
                    if record is None or record["status"] not in ("data","zero"):
                        result={"status":"unavailable"}
                    else:
                        rain,_=get_rainfield_np(source,record["filename"],missing_as_nan=True)
                        if rain.shape!=(domain["n_rows"],domain["n_cols"]):raise ValueError("Shape mismatch")
                        result=estimate(rain,product,domain["p_size"]/1000,scale,cutoff)
                    doc={**key,"source":record,**result}
                except Exception as exc:
                    doc={**key,"source":record,"status":"failed","error":f"{type(exc).__name__}: {exc}"}
                collection.replace_one(key,doc,upsert=True)
            totals[doc["status"]]+=1
            spectrum_totals[doc.get("spectrum",{}).get("status","not_estimated")]+=1
            fit_totals[doc.get("nonzero_fit",{}).get("status","not_estimated")]+=1
            if (i+1)%500==0:print(canonical({"processed":i+1,"outcomes":dict(totals)}),flush=True)
        assert sum(totals.values())==len(selected)
        summary={"run_id":args.run_id,"fields":len(selected),"outcomes":dict(totals),
            "spectrum_outcomes":dict(spectrum_totals),"latent_fit_outcomes":dict(fit_totals),
            "elapsed_seconds":time.monotonic()-started,"status":"failed" if totals["failed"] else "complete"}
        if not existing or existing["status"]!="complete":
            runs.update_one({"_id":args.run_id},{"$set":summary});atomic_json(out/"summary.json",summary)
        print(canonical(summary),flush=True)
    return 1 if totals["failed"] else 0


if __name__=="__main__":raise SystemExit(main())
