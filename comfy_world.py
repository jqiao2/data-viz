"""World map of comfortable days per year on a 2° land grid, for the interactive page.

Run: python comfy_world.py  -> downloads NASA POWER point data (MERRA-2 + CERES, free, no key) at the landiest
0.5° x 0.625° MERRA-2 cell of every 2° land cell to data/raw/comfy/world/, writes output/comfy/web/world.json,
world_hist.txt, output/comfy/world.png and world.csv. First run ~1-2 hours; later runs take under a minute. Same day definitions and histogram format as comfy_weather.py.
"""
import base64
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio.features
import requests
from affine import Affine
from shapely.affinity import affine_transform
from shapely.geometry import box

import comfy_weather as cw
from comfy_weather import BG, BLUES, INK, MM_PER_IN, MUTED, OUT, RAW, SNOW_RATIO, YEARS, c_to_f, hist, pack

STEP = 2          # output cell, degrees
MIN_LAND = 0.05   # keep output cells at least this much land
LAT1 = 84         # map runs lat -60..84
LAT = -60 + 0.5 * np.arange(288)       # MERRA-2 native cell centres
LON = -180 + 0.625 * np.arange(576)
WORLD = RAW / "comfy" / "world"
NE = RAW / "naturalearth" / "ne_50m_admin_0_countries.zip"
NE_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
SCALE = 4         # px per degree: x = (lon + 180) * 4, y = (84 - lat) * 4
XY = [SCALE, 0, 0, -SCALE, 180 * SCALE, LAT1 * SCALE]  # shapely affine for that projection


def countries():
    if not NE.exists():
        NE.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(NE_URL, timeout=300)
        r.raise_for_status()
        NE.write_bytes(r.content)
    return gpd.read_file(NE)


def land_frac(g):
    """Land fraction of every native cell (288 x 576, rows south to north), from a 0.025° raster of the countries."""
    t = Affine(0.025, 0, -180.3125, 0, -0.025, LAT1 + 0.25)
    a = rasterio.features.rasterize(((geom, 1) for geom in g.geometry), out_shape=(289 * 20, 576 * 25),
                                    transform=t, dtype="uint8")
    return a.reshape(289, 20, 576, 25).mean(axis=(1, 3))[1:][::-1]  # drop the row centred at 84


def grid(frac):
    """Kept output cells and, for each land native cell, its output cell index (-1 if dropped)."""
    oi = ((LAT + 60) // STEP).astype(int)[:, None].repeat(len(LON), 1)
    oj = ((LON + 180) // STEP).astype(int)[None, :].repeat(len(LAT), 0)
    flat = oi * int(360 / STEP) + oj
    cell_frac = np.bincount(flat.ravel(), frac.ravel()) / np.bincount(flat.ravel())
    keep = np.flatnonzero(cell_frac >= MIN_LAND)
    cid = np.full(cell_frac.size, -1)
    cid[keep] = np.arange(len(keep))
    centres = [(round(-60 + (k // int(360 / STEP) + 0.5) * STEP, 3), round(-180 + (k % int(360 / STEP) + 0.5) * STEP, 3))
               for k in keep]
    return centres, cid[flat]


# --- downloads ---------------------------------------------------------------------------------------
def cell_path(p):
    return WORLD / "cells" / f"{p[0]:.2f}_{p[1]:.3f}.csv"


def dew_path(p):
    return WORLD / "dew" / f"{p[0]:.2f}_{p[1]:.3f}.npy"


def download(p):
    """Ten years of daily values at native cell p, cached as one CSV in comfy_weather's column layout. Daily high,
    mean dew point, daytime cloud, precipitation and snow come from one daily point request; the day's rise from
    mean to peak dew point from hourly T2MDEW (LST). Cells comfy_weather.py already fetched are reused as they are."""
    if cw.cell_path(p).exists() or cell_path(p).exists():
        return
    if not dew_path(p).exists():
        h = cw.power("hourly", p, "T2MDEW").groupby(["YEAR", "MO", "DY"]).T2MDEW
        dew_path(p).parent.mkdir(parents=True, exist_ok=True)
        np.save(dew_path(p), (h.max() - h.mean()).to_numpy().astype(np.float16))
    d = cw.power("daily", p, "T2M_MAX,T2MDEW,CLOUD_AMT_DAY,PRECTOTCORR,PRECSNOLAND")
    d = d.rename(columns={"T2M_MAX": "tmax", "T2MDEW": "dmean", "CLOUD_AMT_DAY": "cloud", "PRECTOTCORR": "precip",
                          "PRECSNOLAND": "snow"})
    if d.snow.isna().all():  # PRECSNOLAND is undefined over land ice and big lakes; take all-surface snowfall there
        d["snow"] = cw.power("daily", p, "PRECSNO").PRECSNO
    d["dmax"] = d.dmean + np.load(dew_path(p)).astype(float)
    cell_path(p).parent.mkdir(parents=True, exist_ok=True)
    d.round(2).to_csv(cell_path(p), index=False)


def daily(p):
    """Daily DataFrame for native cell p, as comfy_weather.daily."""
    if cw.cell_path(p).exists():
        return cw.daily(p)
    d = pd.read_csv(cell_path(p))
    d.index = pd.to_datetime(dict(year=d.YEAR, month=d.MO, day=d.DY))
    return pd.DataFrame({"tmax": c_to_f(d.tmax), "dmax": c_to_f(d.dmax), "cloud": d.cloud,
                         "rain": (d.precip - d.snow).clip(lower=0) / MM_PER_IN,
                         "snow": d.snow / MM_PER_IN * SNOW_RATIO})


def representative(frac, cell_of, centres):
    """Each output cell's landiest native cell, the one nearest the cell centre on ties."""
    best = {}
    for r, c in zip(*np.nonzero(frac > 0)):
        k = cell_of[r, c]
        if k >= 0:
            key = (frac[r, c], -np.hypot(LAT[r] - centres[k][0], LON[c] - centres[k][1]))
            if key > best.get(k, ((0, 0), None))[0]:
                best[k] = (key, (float(LAT[r]), float(LON[c])))
    return [best[k][1] for k in range(len(centres))]


def encode(hists):
    """base64 of gzip of every packed histogram, in order, and the uint32 count of each (as hist.txt)."""
    packed = np.concatenate([pack(h) for h in hists]).astype("<u4")
    return base64.b64encode(gzip.compress(packed.tobytes(), 9)), [len(h[0]) for h in hists]


def land_path(g):
    g = g.clip(box(-180, -60, 180, LAT1)).explode()
    g = g[g.geom_type == "Polygon"]
    return "".join(cw.svg_path(affine_transform(p, XY).simplify(0.3)) for p in g.geometry)


def world_png(centres, days, g, path):
    a = np.full((int(144 / STEP), int(360 / STEP)), np.nan)
    for (la, lo), d in zip(centres, days):
        a[int((LAT1 - la) // STEP), int((lo + 180) // STEP)] = d
    fig = plt.figure(figsize=(16, 7.4), facecolor=BG)
    ax = fig.add_axes([0, 0.08, 1, 0.82])
    vmax = float(np.ceil(np.nanmax(a) / 10) * 10)
    ax.imshow(a, cmap=BLUES, vmin=0, vmax=vmax, extent=(-180, 180, -60, LAT1), interpolation="nearest")
    g.boundary.plot(ax=ax, color="white", linewidth=0.3)
    ax.set_xlim(-180, 180)
    ax.set_ylim(-60, LAT1)
    ax.set_axis_off()
    cax = fig.add_axes([0.55, 0.05, 0.35, 0.018])
    cb = fig.colorbar(plt.cm.ScalarMappable(cmap=BLUES, norm=plt.Normalize(0, vmax)), cax=cax, orientation="horizontal")
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelsize=10)
    cb.set_label("Comfortable days per year", color=INK, fontsize=11)
    fig.text(0.02, 0.95, "Comfortable days per year, world", fontsize=20, fontweight="bold", color=INK)
    fig.text(0.02, 0.92, cw.criteria() + f" · NASA POWER MERRA-2, {STEP}° land cells", fontsize=11, color=MUTED)
    cw.save(fig, path)


def main():
    g = countries()
    frac = land_frac(g)
    centres, cell_of = grid(frac)
    rep = representative(frac, cell_of, centres)
    print(len(centres), "cells", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(8) as pool:
        for i, _ in enumerate(pool.map(download, rep)):
            if i % 500 == 0:
                print("downloaded", i, f"{time.time() - t0:.0f}s", flush=True)

    ds = [daily(p) for p in rep]
    text, sizes = encode([hist(d) for d in ds])
    web = OUT / "web"
    web.mkdir(parents=True, exist_ok=True)
    (web / "world_hist.txt").write_bytes(text)
    data = dict(years=[YEARS[0], YEARS[-1]], canvas=[360 * SCALE, (LAT1 + 60) * SCALE], step=STEP,
                cells=[list(c) for c in centres], land=land_path(g), sizes=sizes)
    (web / "world.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    days = [cw.comfy(d).sum() / len(YEARS) for d in ds]
    world_png(centres, days, g, OUT / "world.png")
    pd.DataFrame(centres, columns=["lat", "lon"]).assign(src_lat=[p[0] for p in rep], src_lon=[p[1] for p in rep],
                                                         days=np.round(days, 1)).to_csv(OUT / "world.csv", index=False)
    print("wrote", web / "world.json", f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
