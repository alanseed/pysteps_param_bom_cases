#!/usr/bin/env python3
"""Render stored pilot tracks over their previous rainfall input, viewer style.

Adapted from the banked plot_sparse_stage_diagnostic.py. Reuses the core
viewer's projection builder and shared rainfall style; does not rerun LK.
"""
import argparse
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import numpy as np

from pysteps_param import get_db, get_rainfield_np
from pysteps_param.cli.rain_view import make_map_projection
from pysteps_param.rain_style import cmap, norm, _nice_degree_step
from pysteps_param_bom_cases.event_lk import (
    CATALOGUE_PROJECTION, atomic_json, file_identity, utc,
)
from pysteps_param_bom_cases.research_store import ResearchStore, digest


def render(source, store, run, record, output):
    store.read({k: record[k] for k in ("run_id", "storm_id", "valid_time")})
    previous = record["inputs"]["previous"]
    live = source.field_catalogue.find_one({"filename": previous["filename"]}, CATALOGUE_PROJECTION)
    if digest(file_identity(live)) != digest(previous):
        raise ValueError("Rainfall input has changed since pilot extraction")
    rain, _ = get_rainfield_np(source, previous["filename"], missing_as_nan=True)
    domain = run["manifest"]["domain"]
    step = domain["p_size"]
    x = domain["start_x"]+np.arange(domain["n_cols"])*step
    y = domain["start_y"]+np.arange(domain["n_rows"])*step
    projection = make_map_projection(domain)
    raw = store.fs.get(record["payload_id"]).read()
    with np.load(BytesIO(raw), allow_pickle=False) as arrays:
        xy, uv, rejected = (arrays[k].copy() for k in ("xy", "uv", "outlier_mask"))
    fig, ax = plt.subplots(figsize=(12,8), subplot_kw={"projection": projection})
    fig.subplots_adjust(left=.14,right=.90,bottom=.10,top=.84)
    ax.add_feature(cfeature.COASTLINE,edgecolor="gray")
    mesh = ax.pcolormesh(x,y,np.ma.masked_invalid(rain),shading="auto",
                         transform=projection,cmap=cmap,norm=norm)
    fig.colorbar(mesh,ax=ax,orientation="vertical",label="Rainfall (mm/h)",shrink=.82)
    for mask,color,label in ((~rejected,".05","Accepted"),(rejected,"#CC0077","Rejected outlier")):
        starts = xy[mask]*step+[domain["start_x"],domain["start_y"]]
        ax.scatter(starts[:,0],starts[:,1],s=16,facecolors="none",edgecolors=color,
                   linewidths=.7,transform=projection,label=f"{label}: {mask.sum()}",zorder=4)
        ax.quiver(starts[:,0],starts[:,1],uv[mask,0]*step,uv[mask,1]*step,
                  color=color,angles="xy",scale_units="xy",scale=1,width=.0017,
                  transform=projection,zorder=3)
    corners=ccrs.PlateCarree().transform_points(projection,
        np.array([x.min(),x.min(),x.max(),x.max()]),
        np.array([y.min(),y.max(),y.min(),y.max()]))
    gl=ax.gridlines(crs=ccrs.PlateCarree(),draw_labels=True,linestyle="--",linewidth=.5,color="gray")
    gl.top_labels=False;gl.right_labels=False
    for values,attribute in ((corners[:,0],"xlocator"),(corners[:,1],"ylocator")):
        spacing=_nice_degree_step(values.max()-values.min())
        setattr(gl,attribute,mticker.FixedLocator(np.arange(
            np.floor(values.min()/spacing)*spacing,
            np.ceil(values.max()/spacing)*spacing+spacing*.5,spacing)))
    gl.xformatter=LongitudeFormatter();gl.yformatter=LatitudeFormatter()
    gl.xlabel_style={"size":10};gl.ylabel_style={"size":10}
    ax.set_xlim(x[0]-step/2,x[-1]+step/2);ax.set_ylim(y[0]-step/2,y[-1]+step/2)
    ax.legend(loc="upper right",fontsize=9,framealpha=.95)
    start=utc(previous["metadata"]["source"]["source_valid_time"])
    end=utc(record["actual_times"]["current"])
    war=previous["metadata"]["wetted_area_ratio"]
    fig.suptitle(f"Input QPE: {start:%Y-%m-%d %H:%M:%S} UTC\n"
                 f"LK tracks to {end:%H:%M:%S} UTC | Rain coverage: {war:.1%}",y=.98)
    fig.text(.50,.035,"Circles: tracked locations in the previous input; arrows: actual pair displacement (scale 1:1).\n"
             "Black: accepted; magenta: LK outlier. No declustering or dense interpolation.",
             ha="center",fontsize=9)
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=200)
    plt.close(fig)
    return {"figure":str(output),"run_id":record["run_id"],"storm_id":record["storm_id"],
            "pair_valid_time":record["valid_time"],"previous_source_time":start,
            "current_source_time":end,"rain_coverage":war,"counts":record["counts"],
            "payload_sha256":record["payload_sha256"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id",default="pilot_1000_v1")
    parser.add_argument("--output-dir",type=Path,default=Path(__file__).resolve().parents[1]/"figs")
    args=parser.parse_args()
    research=get_db(target_db="BOM_advection");store=ResearchStore(research)
    run=research.runs.find_one({"_id":args.run_id})
    if run is None or run["status"]!="complete":raise ValueError("A completed pilot run is required")
    source=research.client[run["manifest"]["source_database"]]
    records=list(research.pairs.find({"run_id":args.run_id,"outcome":"vectors"}))
    # Illustrative contrasts in rainfall support; not an event sampling protocol.
    selected=[min(records,key=lambda r:abs(r["inputs"]["previous"]["metadata"]["wetted_area_ratio"]-target))
              for target in (.10,.60)]
    results=[]
    for record in selected:
        output=args.output_dir/(args.run_id+"_lk_"+record["valid_time"].strftime("%Y%m%dT%H%M%SZ")+".png")
        results.append(render(source,store,run,record,output));print(output)
    atomic_json(args.output_dir/(args.run_id+"_figures.json"),{
        "selection":"Previous-input rain coverage nearest 10% and 60% among pilot vector pairs; illustrative only",
        "style":"psteps-rain-view shared colormap, boundaries, projection and 12x8 layout",
        "figures":results})


if __name__=="__main__":main()
