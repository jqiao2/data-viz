"""World map of comfortable days per year on a 1° land grid averaged from every native MERRA-2 cell, for the
interactive page, with a 0.25° elevation grid and lapse rates so the page can shift mountains to their real height.

Run: python comfy_world.py  -> downloads NASA POWER regional daily data (MERRA-2 + CERES, free, no key) for every
land 0.5° x 0.625° cell, 2016-2025, to data/raw/comfy/world/region/ (~17,600 requests, 2-2.5 hours the first time)
and ETOPO 2022 (478 MB) to data/raw/etopo/, then writes output/comfy/web/world.json, world_hist_*.txt,
output/comfy/world.png and world.csv. Later runs re-read the cache (about 3 minutes). Same day definitions and
histogram format as comfy_weather.py. See README.md, "World grid".
"""
import base64
import gzip
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import requests
from affine import Affine
from shapely.affinity import affine_transform
from shapely.geometry import box

import comfy_weather as cw
from comfy_weather import BG, BINS, BLUES, INK, MM_PER_IN, MUTED, OUT, RAW, SNOW_RATIO, YEARS, c_to_f, pack

STEP = 1          # output cell, degrees; 0.5° would be ~4x the cells (see README)
LAND_MIN = 0.5    # native cells at least this much land are averaged into an output cell
MAX_FILE = 14.5e6 # bytes per world_hist_<i>.txt (artifact hosting takes 15 MB a file)
LAT1 = 84         # map runs lat -60..84
LAT = -60 + 0.5 * np.arange(288)       # MERRA-2 native cell centres, rows south to north
LON = -180 + 0.625 * np.arange(576)
TILE = (20, 16)   # native rows x cols per regional request: 9.5° x 9.375°, under POWER's 10° limit
PARAMS = ["T2M_MAX", "T2MDEW", "CLOUD_AMT_DAY", "PRECTOTCORR", "PRECSNOLAND"]
REGION = "https://power.larc.nasa.gov/api/temporal/daily/regional"
WORLD = RAW / "comfy" / "world"
NE = RAW / "naturalearth" / "ne_50m_admin_0_countries.zip"
NE_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
NE_LAND = RAW / "naturalearth" / "ne_50m_land.zip"
NE_LAND_URL = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_land.zip"
ETOPO = RAW / "etopo" / "ETOPO_2022_v1_60s_N90W180_surface.nc"
ETOPO_URL = ("https://www.ngdc.noaa.gov/thredds/fileServer/global/ETOPO2022/60s/60s_surface_elev_netcdf/"
             "ETOPO_2022_v1_60s_N90W180_surface.nc")
SCALE = 4         # px per degree: x = (lon + 180) * 4, y = (84 - lat) * 4; also the DEM's resolution
XY = [SCALE, 0, 0, -SCALE, 180 * SCALE, LAT1 * SCALE]  # shapely affine for that projection
NODATA = -32768   # DEM value for ocean
DAYS = pd.date_range(f"{YEARS[0]}-01-01", f"{YEARS[-1]}-12-31")


def fetch(url, path=None):
    """GET with retries for busy responses; writes to path when given."""
    if path is not None and path.exists():
        return
    for attempt in range(7):
        try:
            r = requests.get(url, timeout=600, stream=path is not None)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(r.status_code)
            r.raise_for_status()
            if path is None:
                return r
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.replace(path)
            return
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
            if attempt == 6:
                raise
            time.sleep(10 * 2 ** attempt)


def countries():
    if not NE.exists():
        NE.parent.mkdir(parents=True, exist_ok=True)
        NE.write_bytes(fetch(NE_URL).content)
    return gpd.read_file(NE)


def land_frac(g):
    """Land fraction of every native cell (288 x 576, rows south to north), from a 0.025° raster of the countries."""
    t = Affine(0.025, 0, -180.3125, 0, -0.025, LAT1 + 0.25)
    a = rasterio.features.rasterize(((geom, 1) for geom in g.geometry), out_shape=(289 * 20, 576 * 25),
                                    transform=t, dtype="uint8")
    return a.reshape(289, 20, 576, 25).mean(axis=(1, 3))[1:][::-1]  # drop the row centred at 84


# --- elevation ---------------------------------------------------------------------------------------
def dem(g):
    """0.25° grid on the page canvas (576 x 1440, row 0 at lat 84): mean ETOPO 2022 surface height (m) of the
    land in each pixel, NODATA where under half the pixel is land in the Natural Earth countries."""
    ETOPO.parent.mkdir(parents=True, exist_ok=True)
    fetch(ETOPO_URL, ETOPO)
    with rasterio.open(f"netcdf:{ETOPO.as_posix()}:z") as s:  # 1' cells, row 0 at 90N
        r0 = round((s.transform.f - LAT1) * 60)
        z = s.read(1, window=((r0, r0 + 144 * 60), (0, 360 * 60))).astype(np.float32)
        assert np.allclose([s.transform.a, s.transform.c], [1 / 60, -180]) and z.shape == (8640, 21600)
    land = rasterio.features.rasterize(((geom, 1) for geom in g.geometry), out_shape=z.shape,
                                       transform=Affine(1 / 60, 0, -180, 0, -1 / 60, LAT1), dtype="uint8")
    n = land.reshape(576, 15, 1440, 15).sum(axis=(1, 3))
    s = (z * land).reshape(576, 15, 1440, 15).sum(axis=(1, 3))
    return np.where(n >= 15 * 15 / 2, np.round(s / np.maximum(n, 1)), NODATA).astype(np.int16)


# --- downloads ---------------------------------------------------------------------------------------
def tiles(frac):
    """(r0, c0) of every regional request box holding land."""
    return [(r0, c0) for r0 in range(0, len(LAT), TILE[0]) for c0 in range(0, len(LON), TILE[1])
            if (frac[r0:r0 + TILE[0], c0:c0 + TILE[1]] > 0).any()]


def region_url(t, param, start, end, fmt):
    """POWER drops the first row or column when latitude-min or longitude-min is exactly 0, so those boxes
    start one cell early; callers place the returned cells by their coordinates."""
    r0, c0 = t
    r1, c1 = min(r0 + TILE[0], len(LAT)) - 1, c0 + TILE[1] - 1
    la0, lo0 = LAT[r0] - 0.5 * (LAT[r0] == 0), LON[c0] - 0.625 * (LON[c0] == 0)
    return (f"{REGION}?parameters={param}&community=RE&latitude-min={la0}&latitude-max={LAT[r1]}"
            f"&longitude-min={lo0}&longitude-max={LON[c1]}&start={start}&end={end}&format={fmt}")


def tile_path(param, year, t):
    return WORLD / "region" / param / f"{year}_{t[0]:03d}_{t[1]:03d}.npy"


def download(job):
    """One parameter, one year, one box: cached as float16 (days, land cells of the box), rows south to north."""
    param, year, t, mask = job
    path = tile_path(param, year, t)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    r = fetch(region_url(t, param, f"{year}0101", f"{year}1231", "NETCDF"))
    with tempfile.TemporaryDirectory() as d:  # GDAL's netCDF driver wants a real file
        f = Path(d) / "r.nc"
        f.write_bytes(r.content)
        with rasterio.open(f) as s:  # placed by coordinates: cloud comes on CERES's 1° grid, the rest on MERRA-2's
            a = s.read()
            rows = np.floor((s.transform.f - LAT[t[0]:t[0] + mask.shape[0]]) / -s.transform.e).astype(int)
            cols = np.floor((LON[t[1]:t[1] + mask.shape[1]] - s.transform.c) / s.transform.a).astype(int)
            assert rows.min() >= -1 and cols.min() >= -1 and rows.max() <= s.height and cols.max() <= s.width, (job, s.transform)
            a = a[:, rows.clip(0, s.height - 1)][:, :, cols.clip(0, s.width - 1)]  # a centre on a 1° edge takes the inner cell
    np.save(path.with_suffix(".tmp.npy"), a[:, mask].astype(np.float16))
    path.with_suffix(".tmp.npy").replace(path)  # atomic, so an interrupted run never leaves a short file


def elevations(t, shape):
    """MERRA-2 surface height (m) of every cell in a box, from a one-day JSON request (rows south to north)."""
    path = WORLD / "region" / "elev" / f"{t[0]:03d}_{t[1]:03d}.npy"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        f = fetch(region_url(t, "T2M_MAX", "20200101", "20200101", "JSON")).json()["features"]
        e = np.full(shape, np.nan)
        for x in f:
            lon, lat, z = x["geometry"]["coordinates"]
            i, j = round((lat - LAT[t[0]]) * 2), round((lon - LON[t[1]]) / 0.625)
            if 0 <= i < shape[0] and 0 <= j < shape[1]:
                e[i, j] = z
        np.save(path, e)
    return np.load(path)


def download_all(frac):
    ts = tiles(frac)
    masks = {t: frac[t[0]:t[0] + TILE[0], t[1]:t[1] + TILE[1]] > 0 for t in ts}
    jobs = [(p, y, t, masks[t]) for t in ts for p in PARAMS for y in YEARS]  # box by box, so a partial run is usable
    print(len(ts), "boxes,", len(jobs), "requests", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(6) as pool:  # polite: six at a time, each takes a few seconds
        for i, _ in enumerate(pool.map(download, jobs)):
            if i % 500 == 0:
                print("downloaded", i, f"{time.time() - t0:.0f}s", flush=True)
        # PRECSNOLAND is undefined over land ice and big lakes; take all-surface snowfall in boxes that have any
        snowless = [t for t in ts if np.isnan(np.load(tile_path("PRECSNOLAND", YEARS[0], t)).astype(float)).all(0).any()]
        list(pool.map(download, [("PRECSNO", y, t, masks[t]) for y in YEARS for t in snowless]))
        list(pool.map(lambda t: elevations(t, masks[t].shape), ts))
    return ts, masks


# --- daily peak dew point ----------------------------------------------------------------------------
def rise_samples():
    """(lat, lon) and daily rise from mean to peak dew point (°C, DAYS) of every cell with hourly data: the old
    world sample cells (data/raw/comfy/world/dew/) and comfy_weather's cells (data/raw/comfy/power/)."""
    path = WORLD / "rise.npz"
    if not path.exists():
        pts, rise = [], []
        for f in sorted((WORLD / "dew").glob("*.npy")):
            pts.append([float(v) for v in f.stem.split("_")])
            rise.append(np.load(f))
        for f in sorted(cw.CACHE.glob("*.csv")):
            d = cw.power_raw([float(v) for v in f.stem.split("_")]).reindex(DAYS)
            pts.append([float(v) for v in f.stem.split("_")])
            rise.append((d.dmax - d.dmean).to_numpy().astype(np.float16))
        np.savez(path, pts=np.array(pts), rise=np.array(rise))
    z = np.load(path)
    return z["pts"], z["rise"]


def nearest(pts, lat, lon):
    """Index of the sample nearest each (lat, lon), by great-circle distance."""
    la, lo, pla, plo = map(np.radians, (lat[:, None], lon[:, None], pts[:, 0], pts[:, 1]))
    return (np.cos(la) * np.cos(pla) * np.cos(lo - plo) + np.sin(la) * np.sin(pla)).argmax(1)


# --- per native cell ---------------------------------------------------------------------------------
def bins(tmax, dmax, cloud, rain, snow):
    """comfy_weather.hist's flat bin index for arrays of days (-1 where a day is dropped), vectorised."""
    b = BINS
    ti = np.clip((tmax - b["t0"]) // b["ts"], 0, b["nt"] - 1)
    di = np.clip((dmax - b["d0"]) // b["ds"], 0, b["nd"] - 1)
    ci = np.clip(np.nan_to_num(cloud, nan=100) // b["cs"], 0, b["nc"] - 1)
    ri, si = np.searchsorted(b["rain"], rain, side="right"), np.searchsorted(b["snow"], snow, side="right")
    flat = (((ti * b["nd"] + di) * b["nc"] + ci) * (len(b["rain"]) + 1) + ri) * (len(b["snow"]) + 1) + si
    return np.where(np.isnan(tmax + dmax + rain + snow), -1, np.nan_to_num(flat)).astype(int)


def native(ts, masks, frac):
    """Histogram, MERRA-2 height and 10-year mean high and peak dew point (°F) of every land native cell."""
    pts, rise = rise_samples()
    out = {}
    for t in ts:
        rows, cols = np.nonzero(masks[t])
        rr, cc = rows + t[0], cols + t[1]
        v = {p: np.concatenate([np.load(tile_path(p, y, t)) for y in YEARS]).astype(float) for p in PARAMS}
        if tile_path("PRECSNO", YEARS[0], t).exists():
            alt = np.concatenate([np.load(tile_path("PRECSNO", y, t)) for y in YEARS]).astype(float)
            v["PRECSNOLAND"] = np.where(np.isnan(v["PRECSNOLAND"]).all(0), alt, v["PRECSNOLAND"])
        dmax = v["T2MDEW"] + rise[nearest(pts, LAT[rr], LON[cc])].T.astype(float)
        elev = elevations(t, masks[t].shape)[rows, cols]
        snow = v["PRECSNOLAND"]
        tmax, dmax = c_to_f(v["T2M_MAX"]), c_to_f(dmax)
        flat = bins(tmax, dmax, v["CLOUD_AMT_DAY"], np.clip(v["PRECTOTCORR"] - snow, 0, None) / MM_PER_IN,
                    snow / MM_PER_IN * SNOW_RATIO)
        for k, (r, c) in enumerate(zip(rr, cc)):
            idx, n = np.unique(flat[:, k][flat[:, k] >= 0], return_counts=True)
            out[r, c] = dict(h=(idx.astype(np.int32), n.astype(np.int32)), z=elev[k], frac=frac[r, c],
                             t=np.nanmean(tmax[:, k]), d=np.nanmean(dmax[:, k]))
    return out


def lapse(cells):
    """°F per km from regressing each landy native cell's 10-year mean high (and peak dew point) minus the mean
    of its landy neighbours (5 x 5 cells, ~250 km) on the same difference in MERRA-2 height."""
    keys = [k for k, c in cells.items() if c["frac"] >= LAND_MIN]
    grid = {k: cells[k] for k in keys}
    dz, dt, dd = [], [], []
    for r, c in keys:
        nb = [grid[r + i, (c + j) % len(LON)] for i in range(-2, 3) for j in range(-2, 3)
              if (i or j) and (r + i, (c + j) % len(LON)) in grid]
        if len(nb) >= 8:
            me = grid[r, c]
            dz.append(me["z"] - np.mean([n["z"] for n in nb]))
            dt.append(me["t"] - np.mean([n["t"] for n in nb]))
            dd.append(me["d"] - np.mean([n["d"] for n in nb]))
    dz, dt, dd = np.array(dz) / 1000, np.array(dt), np.array(dd)
    ok = np.isfinite(dt) & np.isfinite(dd)
    fit = lambda y: float(np.sum(dz[ok] * y[ok]) / np.sum(dz[ok] ** 2))
    return dict(t=round(fit(dt), 1), d=round(fit(dd), 1))


# --- output grid -------------------------------------------------------------------------------------
def merge(hs):
    """Sum sparse histograms. Counts over 4095 (the packing's 12 bits) are split over repeated bins, which
    the page adds up like any other entry."""
    idx = np.concatenate([h[0] for h in hs])
    u, inv = np.unique(idx, return_inverse=True)
    n = np.bincount(inv, np.concatenate([h[1] for h in hs])).astype(int)
    reps = -(-n // 4095)
    return np.repeat(u, reps), np.concatenate([[4095] * (k - 1) + [m - 4095 * (k - 1)] for m, k in zip(n, reps)])


def grid(cells, demz, step):
    """Output cells: every step° box with land on the DEM. Each sums the histograms of its native cells (by
    centre) that are at least LAND_MIN land; a box with none takes the landiest native cell centred within
    half a degree of it, so thin coasts and small islands get land weather, not the sea's."""
    by_box = {}
    for (r, c) in cells:
        by_box.setdefault((int((LAT[r] + 60) // step), int((LON[c] + 180) // step)), []).append((r, c))
    k = int(step * SCALE)
    has_land = (demz != NODATA).reshape(144 * SCALE // k, k, 360 * SCALE // k, k).any(axis=(1, 3))[::-1]
    out = []
    for i, j in zip(*np.nonzero(has_land)):
        lat, lon = -60 + (i + 0.5) * step, -180 + (j + 0.5) * step
        use = [p for p in by_box.get((i, j), []) if cells[p]["frac"] >= LAND_MIN]
        if not use:
            near = [p for di in (-1, 0, 1) for dj in (-1, 0, 1)
                    for p in by_box.get((i + di, (j + dj) % int(360 / step)), [])
                    if abs(LAT[p[0]] - lat) <= step / 2 + 0.5 and abs((LON[p[1]] - lon + 180) % 360 - 180) <= step / 2 + 0.5]
            if not near:
                continue
            use = [max(near, key=lambda p: (cells[p]["frac"], -np.hypot(LAT[p[0]] - lat, LON[p[1]] - lon)))]
        out.append(dict(ll=[round(lat, 3), round(lon, 3)], use=use))
    return out


def write_hists(web, hists):
    """base64 of gzip of the packed histograms, split into files of at most MAX_FILE bytes on cell boundaries.
    Returns file names and the uint32 count of each cell."""
    for f in web.glob("world_hist*.txt"):
        f.unlink()
    packed = [pack(h).astype("<u4") for h in hists]
    blobs = [base64.b64encode(gzip.compress(np.concatenate(packed).tobytes(), 9))]
    parts = 1
    while max(map(len, blobs)) > MAX_FILE:
        parts += 1
        cuts = np.array_split(np.arange(len(packed)), parts)
        blobs = [base64.b64encode(gzip.compress(np.concatenate([packed[i] for i in c]).tobytes(), 9)) for c in cuts]
    names = [f"world_hist_{i}.txt" for i in range(len(blobs))]
    for name, b in zip(names, blobs):
        (web / name).write_bytes(b)
    return names, [len(p) for p in packed]


def coast_path():
    """Dissolved Natural Earth 50m land outline in canvas px, the page's clip path."""
    if not NE_LAND.exists():
        NE_LAND.write_bytes(fetch(NE_LAND_URL).content)
    return land_path(gpd.read_file(NE_LAND))


def land_path(g):
    g = g.clip(box(-180, -60, 180, LAT1)).explode()
    g = g[g.geom_type == "Polygon"]
    return "".join(cw.svg_path(affine_transform(p, XY).simplify(0.3)) for p in g.geometry)


# --- static map --------------------------------------------------------------------------------------
def shifted_days(h, dts, dds):
    """Comfortable days per year at the default thresholds after adding dts / dds (°F) to every day's high /
    peak dew point. Days are taken as spread evenly through each 5°F bin."""
    b = BINS
    idx, n = h
    nr, ns = len(b["rain"]) + 1, len(b["snow"]) + 1
    si, j = idx % ns, idx // ns
    ri, j = j % nr, j // nr
    ci, j = j % b["nc"], j // b["nc"]
    di, ti = j % b["nd"], j // b["nd"]
    ok = (ci < cw.CLOUD_MAX / b["cs"]) & (ri < np.searchsorted(b["rain"], cw.RAIN_MAX_IN, side="right")) \
        & (si < np.searchsorted(b["snow"], cw.SNOW_MAX_IN, side="right"))
    tab = np.zeros((b["nt"], b["nd"]))
    np.add.at(tab, (ti[ok], di[ok]), n[ok])
    cum = np.zeros((b["nt"] + 1, b["nd"] + 1))
    cum[1:, 1:] = tab.cumsum(0).cumsum(1)

    def below(x, y):  # days with high bin-coordinate < x and dew < y, bilinear inside bins
        x, y = np.clip(x, 0, b["nt"]), np.clip(y, 0, b["nd"])
        i, j = np.minimum(x.astype(int), b["nt"] - 1), np.minimum(y.astype(int), b["nd"] - 1)
        fx, fy = x - i, y - j
        return ((1 - fx) * (1 - fy) * cum[i, j] + fx * (1 - fy) * cum[i + 1, j]
                + (1 - fx) * fy * cum[i, j + 1] + fx * fy * cum[i + 1, j + 1])

    tx = lambda f: (f - b["t0"]) / b["ts"]
    dy = (cw.DEW_MAX_F - dds - b["d0"]) / b["ds"]
    days = below(tx(cw.HIGH_F[1] - dts), dy) - below(tx(cw.HIGH_F[0] - dts), dy)
    return days / n.sum() * 365.25


def world_png(cells_out, hists, ce, demz, lp, g, path):
    """Each DEM pixel gets its cell's histogram shifted by the lapse rates over (pixel height - cell height)."""
    a = np.full(demz.shape, np.nan)
    k = int(STEP * SCALE)
    for c, h, z in zip(cells_out, hists, ce):
        i0, j0 = int((LAT1 - c["ll"][0] - STEP / 2) * SCALE), int((c["ll"][1] + 180 - STEP / 2) * SCALE)
        sub = demz[i0:i0 + k, j0:j0 + k].astype(float)
        land = sub != NODATA
        dz = (sub[land] - z) / 1000
        blk = a[i0:i0 + k, j0:j0 + k]
        blk[land] = shifted_days(h, lp["t"] * dz, lp["d"] * dz)
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
    fig.text(0.02, 0.92, cw.criteria() + f" · NASA POWER MERRA-2 averaged to {STEP}°, shifted to 0.25° ETOPO terrain",
             fontsize=11, color=MUTED)
    cw.save(fig, path)
    return a


def main():
    t0 = time.time()
    g = countries()
    frac = land_frac(g)
    ts, masks = download_all(frac)
    print("downloads done", f"{time.time() - t0:.0f}s", flush=True)
    demz = dem(g)
    cells = native(ts, masks, frac)
    print(len(cells), "native cells", f"{time.time() - t0:.0f}s", flush=True)
    lp = lapse(cells)
    print("lapse °F/km", lp, flush=True)
    out = grid(cells, demz, STEP)
    hists = [merge([cells[p]["h"] for p in c["use"]]) for c in out]
    ce = [int(round(np.mean([cells[p]["z"] for p in c["use"]]))) for c in out]
    web = OUT / "web"
    web.mkdir(parents=True, exist_ok=True)
    files, sizes = write_hists(web, hists)
    data = dict(years=[YEARS[0], YEARS[-1]], canvas=[360 * SCALE, (LAT1 - -60) * SCALE], step=STEP,
                cells=[c["ll"] for c in out], ce=ce, n=[len(c["use"]) for c in out], files=files, sizes=sizes,
                dem=base64.b64encode(gzip.compress(demz.astype("<i2").tobytes(), 9)).decode(),
                land=land_path(g), coast=coast_path(), lapse=lp)
    (web / "world.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(len(out), "cells;", {f: (web / f).stat().st_size for f in files}, "world.json",
          (web / "world.json").stat().st_size, flush=True)
    world_png(out, hists, ce, demz, lp, g, OUT / "world.png")
    days = [shifted_days(h, np.zeros(1), np.zeros(1))[0] for h in hists]
    pd.DataFrame([c["ll"] for c in out], columns=["lat", "lon"]).assign(
        elev_m=ce, native_cells=[len(c["use"]) for c in out], days=np.round(days, 1)).to_csv(OUT / "world.csv", index=False)
    print("wrote", web / "world.json", f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
