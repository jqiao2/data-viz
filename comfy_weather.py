"""Days of comfortable weather per year, for every US county and ~900 US and world cities.

Run: python comfy_weather.py  -> downloads Daymet 1 km county data and NASA POWER cell data (both free, no key)
to data/raw/comfy/,
writes output/comfy/us_counties.png, cities.png, matching CSVs and the interactive page's data
(output/comfy/web/). City list: data/comfy_cities.csv (see build_cities). Downloads are cached, so changing the
thresholds below and re-running takes seconds. See README.md.
"""
import base64
import csv
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import rasterio.windows
import requests
from matplotlib.colors import LinearSegmentedColormap
from shapely.affinity import affine_transform

from sheep_grid_maps import POP_TIF, SOURCES as SHEEP_SOURCES

# --- what counts as a comfortable day -------------------------------------------------------------
HIGH_F = (25, 70)   # daily high, °F: at least the first, below the second
DEW_MAX_F = 55      # daily max dew point below this, °F
CLOUD_MAX = 60      # daytime cloud cover below this, %
RAIN_MAX_IN = 0.04  # daily rain below this, inches (0.04 in = 1 mm, the usual "wet day" cut)
SNOW_MAX_IN = float("inf")  # daily snowfall below this, inches of snow; inf = snow days are fine
# ----------------------------------------------------------------------------------------------------

YEARS = range(2016, 2026)
RAW = Path("data/raw")
CACHE = RAW / "comfy" / "power"
OUT = Path("output/comfy")
CITIES = Path("data/comfy_cities.csv")
POWER = "https://power.larc.nasa.gov/api/temporal/{}/point"
MONTHS = "JFMAMJJASOND"
BLUES = LinearSegmentedColormap.from_list("blues", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
INK, MUTED, BG = "#1e293b", "#64748b", "#f8fafc"
MM_PER_IN = 25.4
SNOW_RATIO = 10  # inches of snow per inch of water
SNOW_C, RAIN_C = 0.5, 2.0  # daily mean °C: all snow at or below, all rain at or above (as in ikon_snow.py)


def c_to_f(c):
    return c * 9 / 5 + 32


def comfy(df):
    """Boolean Series: is each day comfortable? df has tmax/dmax in °F, cloud in %, rain in inches of water,
    snow in inches of snowfall."""
    return ((df.tmax >= HIGH_F[0]) & (df.tmax < HIGH_F[1]) & (df.dmax < DEW_MAX_F)
            & (df.cloud < CLOUD_MAX) & (df.rain < RAIN_MAX_IN) & (df.snow < SNOW_MAX_IN))


def per_month(daily):
    """Average comfortable days in each calendar month."""
    ok = comfy(daily)
    m = ok.groupby(ok.index.month).sum() / daily.index.year.nunique()
    return [round(float(m.get(i, 0.0)), 1) for i in range(1, 13)]


# --- downloads ---------------------------------------------------------------------------------------
def cell(lat, lon):
    """Centre of the MERRA-2 cell (0.5° x 0.625°) holding (lat, lon); POWER returns the same series anywhere in it."""
    return round(lat * 2) / 2, round((lon + 180) / 0.625) * 0.625 - 180


def cell_path(c):
    return CACHE / f"{c[0]:.2f}_{c[1]:.3f}.csv"


def power(kind, c, params):
    """One POWER point request as a CSV DataFrame (header block stripped), with retries for busy responses."""
    q = dict(parameters=params, community="RE", latitude=c[0], longitude=c[1], format="CSV",
             start=f"{YEARS[0]}0101", end=f"{YEARS[-1]}1231", **({"time-standard": "LST"} if kind == "hourly" else {}))
    for attempt in range(6):
        try:
            r = requests.get(POWER.format(kind), params=q, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(r.status_code)
            r.raise_for_status()
            break
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
            if attempt == 5:
                raise
            time.sleep(10 * 2 ** attempt)
    body = r.text.split("-END HEADER-")[1].strip()
    return pd.read_csv(pd.io.common.StringIO(body), na_values=[-999])


def download(c):
    """Daily high, max dew point and precipitation (from hourly MERRA-2, local standard time) plus daytime
    cloud amount (CERES), snowfall and mean dew point, cached as one small CSV per cell."""
    path = cell_path(c)
    if path.exists():
        return
    h = power("hourly", c, "T2M,T2MDEW,PRECTOTCORR")
    d = h.groupby(["YEAR", "MO", "DY"]).agg(tmax=("T2M", "max"), dmax=("T2MDEW", "max"),
                                             precip=("PRECTOTCORR", "sum")).reset_index()
    dl = power("daily", c, "CLOUD_AMT_DAY,PRECSNOLAND,T2MDEW")
    d = d.merge(dl.rename(columns={"CLOUD_AMT_DAY": "cloud", "PRECSNOLAND": "snow", "T2MDEW": "dmean"}),
                on=["YEAR", "MO", "DY"], how="left")
    path.parent.mkdir(parents=True, exist_ok=True)
    d.round(2).to_csv(path, index=False)


DAYMET = "https://daymet.ornl.gov/single-pixel/api/data"
DAYMET_CACHE = RAW / "comfy" / "daymet"


def daymet_path(lat, lon):
    return DAYMET_CACHE / f"{lat:.4f}_{lon:.4f}.csv.gz"


def download_daymet(p):
    """Daymet v4 daily 1 km values at (lat, lon), cached per point."""
    lat, lon = p
    path = daymet_path(lat, lon)
    if path.exists():
        return
    q = dict(lat=lat, lon=lon, vars="tmax,tmin,prcp,vp", start=f"{YEARS[0]}-01-01", end=f"{YEARS[-1]}-12-31")
    for attempt in range(6):
        try:
            r = requests.get(DAYMET, params=q, timeout=180)
            if r.status_code == 400:  # outside Daymet's grid (western Alaska); point_data falls back to POWER
                return
            r.raise_for_status()
            break
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
            if attempt == 5:
                raise
            time.sleep(10 * 2 ** attempt)
    body = r.text[r.text.index("year,yday"):]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.read_csv(pd.io.common.StringIO(body)).to_csv(path, index=False)


def power_raw(c):
    d = pd.read_csv(cell_path(c))
    d.index = pd.to_datetime(dict(year=d.YEAR, month=d.MO, day=d.DY))
    return d


def daily(c):
    """Daily DataFrame for a POWER cell (used for cities): tmax, dmax (°F), cloud (%, daytime),
    rain (in of water), snow (in of snowfall). Rain is total precipitation minus snow."""
    d = power_raw(c)
    # PRECSNOLAND is land-only, so all-ocean cells (Malé, Majuro, Funafuti) have none; call it no snow.
    # ponytail: a cold all-ocean cell would count its snow as rain; switch to PRECSNO if one is ever added.
    d["snow"] = d.snow.fillna(0) if d.snow.isna().all() else d.snow
    return pd.DataFrame({"tmax": c_to_f(d.tmax), "dmax": c_to_f(d.dmax), "cloud": d.cloud,
                         "rain": (d.precip - d.snow).clip(lower=0) / MM_PER_IN,
                         "snow": d.snow / MM_PER_IN * SNOW_RATIO})


def dew_c(vp_pa):
    """Dew point (°C) from vapour pressure (Pa), Magnus formula."""
    x = np.log(vp_pa / 611.2)
    return 243.5 * x / (17.67 - x)


def point_daily(lat, lon, c):
    """Daily DataFrame for a US point (a county's most populated cell, or a city): Daymet 1 km high, dew point,
    rain and snow there, cloud from its POWER cell c.

    Daymet has only the day's average vapour pressure, so its average dew point gets the cell's rise from
    average to peak that day (POWER max minus mean dew point). Snow is split from Daymet precipitation by
    the day's mean temperature."""
    m = pd.read_csv(daymet_path(lat, lon))
    m.index = pd.to_datetime(m.year.astype(str), format="%Y") + pd.to_timedelta(m.yday - 1, unit="D")
    w = power_raw(c).reindex(m.index)
    tavg = (m["tmax (deg c)"] + m["tmin (deg c)"]) / 2
    snow_frac = ((RAIN_C - tavg) / (RAIN_C - SNOW_C)).clip(0, 1)
    prcp = m["prcp (mm/day)"] / MM_PER_IN
    return pd.DataFrame({"tmax": c_to_f(m["tmax (deg c)"]),
                         "dmax": c_to_f(dew_c(m["vp (Pa)"]) + (w.dmax - w.dmean)),
                         "cloud": w.cloud, "rain": prcp * (1 - snow_frac),
                         "snow": prcp * snow_frac * SNOW_RATIO})


def point_data(lat, lon, c):
    """Daymet where it covers the point, otherwise (western Alaska) the POWER cell alone."""
    return point_daily(lat, lon, c) if daymet_path(lat, lon).exists() else daily(c)


# --- data --------------------------------------------------------------------------------------------
POP_ZIP = RAW / "ghs_pop_2015_30ss.zip"  # GHS-POP 2015, 30", shared with sheep_grid_maps.py
DENSEST = RAW / "comfy" / "densest.csv"


def densest_cells(g):
    """(lat, lon) of each county's most populated 30" (~1 km) GHS-POP cell. A county's mean population centre
    can land where nobody lives (Hawaii County's falls between Hilo and Kona at 2,000 m), and on a 1 km grid
    that matters, so read the weather where the most people actually are."""
    out = []
    with rasterio.open(f"zip://{POP_ZIP.as_posix()}!{POP_TIF}") as src:
        for r in g.itertuples():
            w = rasterio.windows.from_bounds(*r.geometry.bounds, src.transform).round_offsets().round_lengths()
            a = src.read(1, window=w, boundless=True, fill_value=0)
            t = src.window_transform(w)
            a = np.where(rasterio.features.geometry_mask([r.geometry], a.shape, t, invert=True), a, 0)
            i, j = np.unravel_index(a.argmax(), a.shape)
            x, y = t * (j + 0.5, i + 0.5)
            out.append((r.GEOID, round(y, 4), round(x, 4)))
    return pd.DataFrame(out, columns=["GEOID", "lat", "lon"])


NE_ZIP = RAW / "ne_10m_populated_places_simple.zip"
NE_URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_populated_places_simple.zip"
US = "United States"


def build_cities(n_us=350, n_world=550):
    """Rewrite CITIES from Natural Earth populated places: the n_us largest US cities by pop_max (plus each
    state's largest) and n_world elsewhere (every capital, each country's largest city if over 1M, the rows
    already in CITIES, then the largest of the rest). Existing rows keep their name and coordinates and take
    the population of the Natural Earth place within 0.2° (0 if none)."""
    if not NE_ZIP.exists():
        r = requests.get(NE_URL, timeout=300)
        r.raise_for_status()
        NE_ZIP.write_bytes(r.content)
    ne = gpd.read_file(NE_ZIP)
    ne = pd.DataFrame(dict(name=ne.name.str.split().str.join(" "), lat=ne.latitude, lon=ne.longitude,
                           country=ne.adm0name.replace({"United States of America": US}), pop=ne.pop_max,
                           cap=ne.adm0cap == 1, state=ne.adm1name)).sort_values("pop", ascending=False)
    us, world = ne[ne.country == US], ne[ne.country != US]
    us = us[us.index.isin(us.index[:n_us]) | us.index.isin(us.groupby("state").head(1).index)]
    old = pd.read_csv(CITIES)
    for i, r in old.iterrows():
        d = np.hypot(world.lat - r.lat, (world.lon - r.lon) * np.cos(np.radians(r.lat)))
        j = d.idxmin()
        old.loc[i, "pop"] = world["pop"][j] if d[j] < 0.2 else 0
        if d[j] < 0.2:
            old.loc[i, "country"] = world.country[j]
            world = world.drop(j)
    big = world.groupby("country").head(1)
    must = world.cap | world.index.isin(big.index[big["pop"] >= 1e6])
    rest = world[~must].head(n_world - len(old) - must.sum())
    out = pd.concat([us, old, world[must], rest])[["name", "country", "lat", "lon", "pop"]]
    out = out.round({"lat": 2, "lon": 2}).astype({"pop": int}).sort_values("pop", ascending=False)
    out.to_csv(CITIES, index=False, encoding="utf-8")


def counties():
    g = gpd.read_file(RAW / "cb_2023_us_county_20m.zip")
    g = g[~g.STATEFP.isin(["72"])]  # Puerto Rico
    if not DENSEST.exists():
        DENSEST.parent.mkdir(parents=True, exist_ok=True)
        densest_cells(g).to_csv(DENSEST, index=False)
    return g.merge(pd.read_csv(DENSEST, dtype={"GEOID": str}), on="GEOID")


def fetch_census():
    for name, url in {
        "cb_2023_us_county_20m.zip": "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_20m.zip",
        POP_ZIP.name: SHEEP_SOURCES[POP_ZIP.name],
    }.items():
        if not (RAW / name).exists():
            with requests.get(url, timeout=600, stream=True) as r, open(RAW / name, "wb") as f:
                r.raise_for_status()
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)


# --- charts ------------------------------------------------------------------------------------------
def criteria():
    return (f"High {HIGH_F[0]}–{HIGH_F[1]}°F · dew point under {DEW_MAX_F}°F · "
            f"daytime cloud under {CLOUD_MAX}% · rain under {RAIN_MAX_IN} in"
            + ("" if SNOW_MAX_IN == float("inf") else f" · snow under {SNOW_MAX_IN} in")
            + f" · {YEARS[0]}–{YEARS[-1]}")


def save(fig, path):
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def county_map(g, path):
    fig = plt.figure(figsize=(16, 10.5), facecolor=BG)
    vmax = float(np.ceil(g.days.max() / 10) * 10)
    kw = dict(column="days", cmap=BLUES, vmin=0, vmax=vmax, edgecolor="white", linewidth=0.15)
    main = fig.add_axes([0, 0.1, 1, 0.82])
    g[~g.STATEFP.isin(["02", "15"])].to_crs(5070).plot(ax=main, **kw)
    ak = fig.add_axes([0.01, 0.08, 0.25, 0.28])
    g[g.STATEFP == "02"].to_crs(3338).plot(ax=ak, **kw)
    hi = fig.add_axes([0.27, 0.08, 0.13, 0.14])
    g[g.STATEFP == "15"].to_crs(32604).plot(ax=hi, **kw)
    for ax in (main, ak, hi):
        ax.set_axis_off()
    ak.set_xlim(-2.3e6, 1.6e6)  # drops the far western Aleutians for size
    cax = fig.add_axes([0.55, 0.05, 0.35, 0.018])
    sm = plt.cm.ScalarMappable(cmap=BLUES, norm=plt.Normalize(0, vmax))
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelsize=10)
    cb.set_label("Comfortable days per year", color=INK, fontsize=11)
    top = g.sort_values("days", ascending=False).head(5)
    lines = "\n".join(f"{r.NAME}, {r.STUSPS}  {r.days:.0f}" for r in top.itertuples())
    fig.text(0.6, 0.24, "Most comfortable days\n" + lines, fontsize=10, color=INK, va="top", linespacing=1.5)
    fig.text(0.02, 0.97, "Comfortable days per year, by county", fontsize=20, fontweight="bold", color=INK)
    fig.text(0.02, 0.935, criteria() + " · read at each county's most populated 1 km",
             fontsize=11, color=MUTED)
    save(fig, path)


def city_chart(rows, path):
    rows = sorted(rows, key=lambda r: -r["year"])
    m = np.array([r["months"] for r in rows])
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(12, 0.27 * len(rows) + 1.8), facecolor=BG,
                                 gridspec_kw=dict(width_ratios=[3, 1.2], wspace=0.04))
    fig.subplots_adjust(top=1 - 1.3 / fig.get_figheight())
    ax.imshow(m, cmap=BLUES, vmin=0, vmax=31, aspect="auto")
    for (i, j), v in np.ndenumerate(m):
        if v >= 1:
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=7.5, color="white" if v > 16 else INK)
    ax.set_xticks(range(12), list(MONTHS))
    ax.set_yticks(range(len(rows)), [f"{r['name']}, {r['country']}" for r in rows], fontsize=9)
    ax.xaxis.tick_top()
    ax.tick_params(length=0, colors=INK)
    ax.set_facecolor(BG)
    bx.barh(range(len(rows)), [r["year"] for r in rows], color="#2a78d6", height=0.7)
    for i, r in enumerate(rows):
        bx.text(r["year"] + 3, i, f"{r['year']:.0f}", va="center", fontsize=8.5, color=INK)
    bx.set_ylim(len(rows) - 0.5, -0.5)
    bx.set_xlim(0, max(r["year"] for r in rows) * 1.18)
    bx.set_axis_off()
    bx.set_title("Days / year", fontsize=10, color=MUTED, loc="left")
    for s in ax.spines.values():
        s.set_visible(False)
    fig.suptitle("Comfortable days by month, the 80 most comfortable cities", x=0.02, ha="left", fontsize=16,
                 fontweight="bold", color=INK, y=1 - 0.4 / fig.get_figheight())
    fig.text(0.02, 1 - 0.7 / fig.get_figheight(), criteria(), fontsize=9.5, color=MUTED)
    save(fig, path)


# --- web page data -----------------------------------------------------------------------------------
# The page recounts comfortable days for any thresholds from per-place histograms of (high, dew point, cloud,
# rain, snow), so the sliders use the same steps as the bins. End bins are open-ended.
BINS = dict(t0=-30, ts=5, nt=30, d0=-40, ds=5, nd=25, cs=10, nc=11,
            rain=[0.01, 0.04, 0.1, 0.25], snow=[0.1, 1, 3, 6])
CANVAS = (960, 610)
INSETS = {"conus": (5070, (0, 0, 960, 610)), "02": (3338, (10, 455, 230, 150)), "15": (32604, (250, 520, 110, 80))}


def hist(d):
    """Sparse histogram: sorted flat bin indices and day counts. Missing cloud (polar night) lands in the
    overcast bin, so it only counts when cloud is set to Any; days missing anything else are dropped."""
    b = BINS
    d = d.dropna(subset=["tmax", "dmax", "rain", "snow"])
    ti = ((d.tmax - b["t0"]) // b["ts"]).clip(0, b["nt"] - 1)
    di = ((d.dmax - b["d0"]) // b["ds"]).clip(0, b["nd"] - 1)
    ci = (d.cloud.fillna(100) // b["cs"]).clip(0, b["nc"] - 1)
    ri = np.searchsorted(b["rain"], d.rain, side="right")
    si = np.searchsorted(b["snow"], d.snow, side="right")
    nr, ns = len(b["rain"]) + 1, len(b["snow"]) + 1
    flat = (((ti * b["nd"] + di) * b["nc"] + ci) * nr + ri) * ns + si
    return np.unique(flat.astype(int), return_counts=True)


def pack(h):
    """One uint32 per bin: index step from the previous bin in the high 20 bits, day count in the low 12."""
    idx, n = h
    step = np.diff(idx, prepend=0)
    assert step.max() < 2 ** 20 and n.max() < 2 ** 12
    return step.astype(np.uint32) * 4096 + n.astype(np.uint32)


def svg_path(geom):
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    return "".join("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in ring.coords[:-1]) + "Z"
                   for poly in polys for ring in (poly.exterior, *poly.interiors))


def insets(g):
    """{inset: (its counties projected, CRS, affine onto CANVAS)}: Albers lower 48, Alaska and Hawaii insets
    bottom-left."""
    out = {}
    for key, (crs, (x0, y0, w, h)) in INSETS.items():
        part = g[~g.STATEFP.isin(["02", "15"])] if key == "conus" else g[g.STATEFP == key]
        part = part.to_crs(crs)
        if key == "02":  # Aleutians past this run off the inset
            part = part.clip((-2.3e6, 0, 1.6e6, 2.5e6))
        minx, miny, maxx, maxy = part.total_bounds
        k = min(w / (maxx - minx), h / (maxy - miny))
        out[key] = part, crs, [k, 0, 0, -k, x0 - minx * k, y0 + maxy * k]
    return out


def county_paths(ins):
    """{GEOID: SVG path} pre-projected to CANVAS."""
    out = {}
    for part, _, t in ins.values():
        for geoid, geom in zip(part.GEOID, part.geometry):
            geom = affine_transform(geom, t).simplify(0.4)
            if not geom.is_empty:
                out[geoid] = svg_path(geom)
    return out


def canvas_xy(ins, lat, lon):
    """[x, y] of a US point on CANVAS, through the same inset projection as the county paths."""
    key = "02" if lon < -129 and lat > 45 else "15" if lon < -150 else "conus"
    _, crs, t = ins[key]
    p = affine_transform(gpd.GeoSeries(gpd.points_from_xy([lon], [lat]), crs=4326).to_crs(crs).iloc[0], t)
    return [round(p.x, 1), round(p.y, 1)]


def zoom_tiers(n, tiers):
    """Minimum label zoom for each of n places ranked by population: tiers is [(count, zoom), ...], the rest 8."""
    z = [zz for k, zz in tiers for _ in range(k)]
    return (z + [8] * n)[:n]


def write_hists(path, hists):
    """Every histogram packed in order, gzipped and base64'd because artifact hosting serves only text."""
    packed = np.concatenate([pack(h) for h in hists]).astype("<u4")
    path.write_bytes(base64.b64encode(gzip.compress(packed.tobytes(), 9)))
    return [len(h[0]) for h in hists]


def export_web(g, county_hists, cities, city_daily, out):
    """data.json (shapes, names, bins, cities, entries per histogram) + hist.txt (one histogram per county)
    + cities_hist.txt (each city's 12 months, in data.json order). cities come sorted by population."""
    ins = insets(g)
    paths = county_paths(ins)
    out.mkdir(parents=True, exist_ok=True)
    sizes = write_hists(out / "hist.txt", [county_hists[r.GEOID] for r in g.itertuples()])
    city_sizes = write_hists(out / "cities_hist.txt",
                             [hist(d[d.index.month == m]) for d in city_daily for m in range(1, 13)])
    us = [c["country"] == US for c in cities]
    zs = {True: iter(zoom_tiers(sum(us), [(15, 1), (35, 2), (100, 4)])),
          False: iter(zoom_tiers(len(us) - sum(us), [(30, 1), (70, 2), (200, 4)]))}
    city_rows = []
    for c, u in zip(cities, us):
        row = dict(n=c["name"], c=c["country"], lat=round(c["lat"], 2), lon=round(c["lon"], 2),
                   p=int(c["pop"]), z=next(zs[u]))
        if u:
            row["xy"] = canvas_xy(ins, c["lat"], c["lon"])
        city_rows.append(row)
    data = dict(years=[YEARS[0], YEARS[-1]], bins=BINS, canvas=CANVAS, sizes=sizes, city_sizes=city_sizes,
                counties=[dict(id=r.GEOID, n=f"{r.NAME}, {r.STUSPS}", d=paths.get(r.GEOID, "")) for r in g.itertuples()],
                cities=city_rows)
    (out / "data.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fetch_census()
    g = counties()
    g["cell"] = [cell(la, lo) for la, lo in zip(g.lat, g.lon)]
    if "pop" not in CITIES.read_text(encoding="utf-8").splitlines()[0]:
        build_cities()
    with open(CITIES, encoding="utf-8") as f:
        cities = sorted(csv.DictReader(f), key=lambda c: -int(c["pop"]))
    for c in cities:
        c["lat"], c["lon"] = float(c["lat"]), float(c["lon"])
        c["cell"] = cell(c["lat"], c["lon"])
    us_pts = [(c["lat"], c["lon"]) for c in cities if c["country"] == US]
    # "Portland, OR": state from the nearest county, since many US city names repeat
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy([p[1] for p in us_pts], [p[0] for p in us_pts]), crs=4326)
    st = gpd.sjoin_nearest(pts.to_crs(5070), g[["STUSPS", "geometry"]].to_crs(5070)).groupby(level=0).STUSPS.first()
    for c, s in zip((c for c in cities if c["country"] == US), st.sort_index()):
        c["name"] += f", {s}"
    todo = sorted(set(g.cell) | {c["cell"] for c in cities})
    print(len(todo), "POWER cells,", len(g), "Daymet counties,", len(us_pts), "Daymet cities", flush=True)
    with ThreadPoolExecutor(10) as pool:  # each request takes seconds; ten at once keeps a first run near an hour
        list(pool.map(download, todo))
        list(pool.map(download_daymet, [(r.lat, r.lon) for r in g.itertuples()] + us_pts))

    county_hists, months = {}, {}
    for r in g.itertuples():
        d = point_data(r.lat, r.lon, r.cell)
        county_hists[r.GEOID], months[r.GEOID] = hist(d), per_month(d)
    g["months"] = g.GEOID.map(months)
    g["days"] = g.months.map(sum)
    county_map(g, OUT / "us_counties.png")
    write_csv(OUT / "us_counties.csv", ["geoid", "county", "state", "days", *MONTHS],
              sorted(([r.GEOID, r.NAME, r.STUSPS, round(r.days, 1), *r.months] for r in g.itertuples()),
                     key=lambda x: -x[3]))

    city_daily = [point_data(c["lat"], c["lon"], c["cell"]) if c["country"] == US else daily(c["cell"])
                  for c in cities]
    rows = [dict(name=c["name"], country=c["country"], months=(ms := per_month(d)), year=sum(ms))
            for c, d in zip(cities, city_daily)]
    city_chart(sorted(rows, key=lambda r: -r["year"])[:80], OUT / "cities.png")  # all ~900 would be unreadable
    write_csv(OUT / "cities.csv", ["city", "country", "days", *MONTHS],
              [[r["name"], r["country"], round(r["year"], 1), *r["months"]] for r in sorted(rows, key=lambda r: -r["year"])])
    export_web(g, county_hists, cities, city_daily, OUT / "web")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
