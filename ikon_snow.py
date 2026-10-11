"""Snowfall during El Niño seasons at every Ikon Base Pass (2026-27) resort.

Run: python ikon_snow.py  -> downloads ERA5 hourly point time series (Copernicus CDS; needs a free key in
~/.cdsapirc) and NOAA ONI to data/raw/, writes one chart per resort to output/ikon/ plus output/ikon/summary.png.
Resort list: data/ikon_base_2026_27.csv. See README.md for method.
"""
import csv
import re
import time
from math import erf
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import cdsapi
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

RAW = Path("data/raw")
OUT = Path("output/ikon")
RESORTS = Path("data/ikon_base_2026_27.csv")
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
START = "1950-01-01"
NORMAL = (1991, 2020)
CM_PER_IN = 2.54
SNOW_C, RAIN_C = 0.5, 2.0  # rain/snow transition, °C at 2 m
CM_PER_MM_WATER = 0.7  # 7:1 snow-to-water ratio
LAPSE = 0.0065  # °C per metre, standard atmosphere
GROUPS = [  # (name, lower ONI bound, colour, range label)
    ("Weak", 0.5, "#56a5d8", "+0.5–0.9°C"),
    ("Moderate", 1.0, "#3f9e80", "+1.0–1.4°C"),
    ("Strong", 1.5, "#f0a23e", "+1.5–1.9°C"),
    ("Very strong", 2.0, "#d9605a", "+2.0°C+"),
]
NH_MONTHS = ("NDJ", "DJF", "JFM")  # peak-winter ONI for a Jul–Jun season
SH_MONTHS = ("MJJ", "JJA", "JAS")  # peak-winter ONI for a Jan–Dec southern season


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def mid_m(r):
    return (float(r["base_m"]) + float(r["summit_m"])) / 2


def oni():
    """{(season code, year): ONI anomaly}. NDJ is labelled with the November year."""
    path = RAW / "oni.txt"
    if not path.exists():
        r = requests.get(ONI_URL, timeout=60)
        r.raise_for_status()
        path.write_text(r.text, encoding="utf-8")
    rows = path.read_text(encoding="utf-8").split("\n")[1:]
    return {(s, int(y)): float(a) for s, y, _, a in (r.split() for r in rows if r.strip())}


def era5_path(r):
    return RAW / "era5" / f"{slug(r['name'])}.zip"


def download(r):
    """Hourly ERA5 at the grid point nearest the resort, 1950 to present (zipped CSV, ~10 MB, ~1 min)."""
    path = era5_path(r)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    cdsapi.Client(quiet=True, progress=False).retrieve("reanalysis-era5-single-levels-timeseries", {
        "variable": ["2m_temperature", "total_precipitation", "surface_pressure", "mean_sea_level_pressure"],
        "location": {"latitude": float(r["lat"]), "longitude": float(r["lon"])},
        "date": [f"{START}/{date.today().isoformat()}"], "data_format": "csv"}, str(path) + ".part")
    Path(str(path) + ".part").rename(path)
    print("downloaded", r["name"], flush=True)


def snow_fraction(t):
    """Share of precipitation falling as snow at air temperature t (°C): all snow at or below SNOW_C,
    all rain at or above RAIN_C, linear in between."""
    return ((RAIN_C - t) / (RAIN_C - SNOW_C)).clip(0, 1)


def cell_elevation(df):
    """ERA5 grid-cell surface height (m) from the hypsometric equation between sea-level and surface pressure."""
    ln = np.log(df.msl / df.sp)
    z = 29.27 * df.t2m * ln  # R/g * T; T in K
    return float((29.27 * (df.t2m + LAPSE * z / 2) * ln).median())  # mean layer temperature, one refinement


def snowfall(r):
    """Daily snowfall (cm) at the resort's mid-mountain elevation, as a pandas Series.

    ERA5's own snowfall is decided on terrain smoothed to ~28 km, often 1,000 m+ below the resort, so warm storms
    come out as rain. Instead lapse-rate adjust ERA5's 2 m temperature from the grid cell's height to mid-mountain
    and split hourly precipitation into rain and snow ourselves."""
    download(r)
    df = pd.read_csv(era5_path(r), usecols=["valid_time", "t2m", "msl", "sp", "tp"],
                     parse_dates=["valid_time"], index_col="valid_time")
    t = df.t2m - 273.15 - LAPSE * (mid_m(r) - cell_elevation(df))
    snow = df.tp * 1000 * snow_fraction(t) * CM_PER_MM_WATER  # tp is metres of water per hour
    return snow.resample("D").sum(min_count=1)


def seasons(r, daily, enso):
    """DataFrame of seasons: label, start year, total (in), ONI peak, complete flag."""
    south = r["hemisphere"] == "S"
    last = daily.dropna().index.max()
    rows = []
    for y in range(1950, last.year + 1):
        a, b = (f"{y}-01-01", f"{y}-12-31") if south else (f"{y}-07-01", f"{y + 1}-06-30")
        if pd.Timestamp(b) > last + timedelta(days=200):  # season hasn't really started
            continue
        keys = [(m, y) for m in SH_MONTHS] if south else [("NDJ", y), ("DJF", y + 1), ("JFM", y + 1)]
        vals = [enso[k] for k in keys if k in enso]
        rows.append(dict(label=str(y) if south else f"{y}–{str(y + 1)[2:]}", year=y,
                         total=daily[a:b].sum() / CM_PER_IN, oni=max(vals) if vals else float("nan"),
                         complete=pd.Timestamp(b) <= last))
    df = pd.DataFrame(rows)
    normal = df[df.year.between(*NORMAL) & df.complete].total.mean()
    df["dep"] = df.total - normal
    df["pct"] = df.dep / normal * 100
    df["group"] = pd.cut(df.oni, [g[1] for g in GROUPS] + [99], right=False, labels=[g[0] for g in GROUPS])
    return df, normal


def fmt_total(inches, metric):
    return f'{inches:.0f}" · {inches * CM_PER_IN:.0f} cm' if metric else f'{inches:.1f}"'


def save(fig, path):
    # ponytail: Windows sometimes holds a fresh PNG open (antivirus/preview) and savefig fails with EINVAL; retry
    for attempt in range(5):
        try:
            fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
            break
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2)
    plt.close(fig)


def chart(r, df, normal, path):
    nino = df.dropna(subset=["group"])
    metric = r["country"] not in ("USA", "United States")
    south = r["hemisphere"] == "S"
    bars, y, sep, centres = [], 0, [], []
    for name, _, colour, rng in GROUPS:
        g = nino[nino.group == name].sort_values("year")
        if g.empty:
            continue
        if bars:
            sep.append(y - 0.5)
            y += 0.4
        centres.append((y + (len(g) - 1) / 2, name, rng))
        for _, s in g.iterrows():
            bars.append((y, s, colour))
            y += 1

    fig, ax = plt.subplots(figsize=(13, 2.6 + 0.37 * len(bars)), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    span = max(abs(nino.dep).max(), 1)
    for yy, s, colour in bars:
        ax.barh(yy, s.dep, color=colour, height=0.66, alpha=1 if s.complete else 0.55,
                hatch=None if s.complete else "//", edgecolor="white", linewidth=0)
        sign = 1 if s.dep >= 0 else -1
        lbl = f'{round(s.dep, 1) + 0:+.1f}" ({round(s.pct) + 0:+d}%)' + ("" if s.complete else " to date")
        ax.text(s.dep + sign * span * 0.015, yy, lbl, va="center", ha="left" if sign > 0 else "right",
                fontsize=9.5, color="#334155")
        inside = fmt_total(s.total, metric)
        fits = abs(s.dep) > span * (0.32 if metric else 0.2)
        ax.text(s.dep / 2 if fits else -sign * span * 0.015, yy, inside, va="center",
                ha="center" if fits else ("right" if sign > 0 else "left"),
                fontsize=9, fontweight="bold", color="white" if fits else "#1e293b")
    ax.set_yticks([b[0] for b in bars], [b[1].label for b in bars], fontsize=10.5, color="#334155")
    ax.invert_yaxis()
    ax.set_ylim(y - 0.3, -0.7)
    ax.set_xlim(-span * 1.45, span * 1.45)
    for s in sep:
        ax.axhline(s + 0.2, color="#dbe3ec", lw=1)
    ax.axvline(0, color="#334155", lw=1.6)
    ax.grid(axis="x", color="#e2e8f0")
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", colors="#475569")
    ax.set_xlabel(f"Snowfall departure from {NORMAL[0]}–{NORMAL[1]} normal (inches)", fontsize=11)
    for cy, name, rng in centres:
        ax.annotate(name, (0, cy), xycoords=("figure fraction", "data"), xytext=(92, -2),
                    textcoords="offset points", ha="right", fontsize=12, fontweight="bold", color="#1e293b")
        ax.annotate(rng, (0, cy), xycoords=("figure fraction", "data"), xytext=(92, -14),
                    textcoords="offset points", ha="right", fontsize=8.5, color="#64748b")

    season = "Jan 1–Dec 31 totals" if south else "July 1–June 30 totals"
    window = "MJJ, JJA, or JAS" if south else "NDJ, DJF, or JFM"
    normal_txt = f'{normal:.1f}"' + (f" ({normal * CM_PER_IN:.0f} cm)" if metric else "")
    where = r["region"] + ", " + r["country"] if r["region"] else r["country"]
    fig.suptitle(f"{r['name']} ({where}): Snowfall During El Niño {'Seasons' if south else 'Winters'}",
                 fontsize=16, fontweight="bold", color="#1e293b", y=0.985)
    fig.text(0.5, 1 - 0.75 / fig.get_figheight(),
             f"{season}; normal = {normal_txt}. Strength is the highest ONI reading in {window}.",
             ha="center", fontsize=10.5, color="#64748b")
    mid = mid_m(r)
    fig.text(0.01, 0.35 / fig.get_figheight(),
             f"Source: ERA5 hourly reanalysis (Copernicus CDS), grid point nearest {r['lat']}, {r['lon']}; NOAA CPC ONI. "
             f"Ikon Base access: {r['access']}.\n"
             f"Precipitation counts as snow below {SNOW_C}°C and rain above {RAIN_C}°C, with temperature adjusted to "
             f"{mid:.0f} m (mid-mountain); 7:1 snow:water.\n"
             "Modelled totals differ from resort-reported ones; read the departures and percentages, not the absolute "
             "numbers. Hatched = season in progress.",
             fontsize=8, color="#64748b", va="bottom")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c, _ in GROUPS]
    fig.legend(handles, [g[0] for g in GROUPS], title="Strength group", ncol=4, loc="lower right",
               frameon=False, fontsize=9, title_fontsize=9, bbox_to_anchor=(0.99, 0))
    fig.subplots_adjust(left=0.2, right=0.98, top=1 - 1.15 / fig.get_figheight(),
                        bottom=1.15 / fig.get_figheight())
    save(fig, path)


REGIONS = {"North America": {"USA", "Canada"}, "Europe": {"France", "Italy", "Andorra", "Austria", "Switzerland"},
           "Asia": {"Japan", "China", "South Korea"}}
ABOVE, BELOW = "#2a78c4", "#d9605a"  # diverging poles: forecast above / below normal


def region(r):
    if r["hemisphere"] == "S":
        return "Southern Hemisphere"
    return next(k for k, v in REGIONS.items() if r["country"] in v)


def fit(df, x_new):
    """Least-squares line of % departure on peak ONI over every complete season, La Niña included.

    Returns slope (% per +1 ONI), correlation r, its t-statistic, the predicted departure at x_new, the residual
    standard deviation, and P(departure > 0) assuming normally distributed residuals. Using all seasons instead of
    only the ~9 strong El Niño winters is what makes the ranking stable."""
    d = df[df.complete].dropna(subset=["oni"])
    x, y = d.oni.to_numpy(), d.pct.to_numpy()
    slope, icpt = np.polyfit(x, y, 1)
    r = np.corrcoef(x, y)[0, 1]
    sd = np.std(y - (icpt + slope * x), ddof=2)
    pred = icpt + slope * x_new
    return dict(n=len(x), slope=slope, r=r, t=r * np.sqrt((len(x) - 2) / (1 - r * r)), pred=pred, sd=sd,
                p_above=0.5 * (1 + erf(pred / sd / np.sqrt(2))))


def summary(stats, x_new, path):
    """Northern Hemisphere forecast: predicted departure at the current ONI with a ±1 SD range, by region."""
    order = ["North America", "Europe", "Asia"]
    rows = []
    for reg in order:
        rows += [None] + sorted((s for s in stats if s["region"] == reg), key=lambda s: s["p_above"])[::-1]
    fig, ax = plt.subplots(figsize=(11, 1.8 + 0.27 * len(rows)), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    ticks, labels = [], []
    for i, s in enumerate(rows):
        if s is None:
            reg = order[sum(r is None for r in rows[:i + 1]) - 1]
            ax.text(-0.01, i, reg, transform=ax.get_yaxis_transform(), ha="right", va="center", fontsize=11,
                    fontweight="bold", color="#1e293b")
            continue
        colour = ABOVE if s["pred"] >= 0 else BELOW
        ax.plot([s["pred"] - s["sd"], s["pred"] + s["sd"]], [i, i], color=colour, alpha=0.25, lw=5,
                solid_capstyle="round", zorder=1)
        ax.scatter(s["pred"], i, color=colour, s=40, zorder=2, edgecolor="#f8fafc", linewidth=1.5)
        ax.text(1.01, i, f"{s['p_above'] * 100:.0f}%", transform=ax.get_yaxis_transform(), va="center",
                fontsize=9, color="#1e293b", fontweight="bold")
        ax.text(1.085, i, f"{s['r']:+.2f}", transform=ax.get_yaxis_transform(), va="center", fontsize=9,
                color="#64748b")
        ticks.append(i)
        labels.append(s["label"])
    ax.text(1.01, -1.4, "P(above)", transform=ax.get_yaxis_transform(), fontsize=8.5, color="#64748b")
    ax.text(1.09, -1.4, "r", transform=ax.get_yaxis_transform(), fontsize=8.5, color="#64748b")
    ax.set_yticks(ticks, labels, fontsize=9, color="#334155")
    ax.invert_yaxis()
    ax.set_ylim(len(rows) - 0.4, -2)
    ax.axvline(0, color="#334155", lw=1.2)
    ax.grid(axis="x", color="#e2e8f0")
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel(f"Predicted snowfall departure from {NORMAL[0]}–{NORMAL[1]} normal (%)", fontsize=10.5)
    fig.suptitle(f"Ikon Base Pass resorts: 2026–27 snowfall outlook at ONI +{x_new:.1f}", fontsize=15,
                 fontweight="bold", color="#1e293b", x=0.02, ha="left")
    fig.text(0.02, 1 - 0.7 / fig.get_figheight(),
             "Dot = predicted departure from a line fit of every winter since 1950-51 against its peak ONI; band = ±1 "
             "SD of past\nwinters around that line. P(above) = chance of beating normal; r = strength of the "
             "ENSO link. ERA5 modelled snowfall.", fontsize=9, color="#64748b", va="top")
    fig.subplots_adjust(left=0.27, right=0.89, top=1 - 1.15 / fig.get_figheight(), bottom=0.6 / fig.get_figheight())
    save(fig, path)


def latest_oni(enso):
    """Most recent ONI reading; the outlook is evaluated at this value."""
    order = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]
    return enso[max(enso, key=lambda k: (k[1], order.index(k[0])))]


def main():
    enso = oni()
    x_new = latest_oni(enso)
    OUT.mkdir(parents=True, exist_ok=True)
    stats = []
    with open(RESORTS, encoding="utf-8") as f:
        resorts = list(csv.DictReader(f))
    with ThreadPoolExecutor(4) as pool:  # CDS queues requests; a few at once is plenty
        list(pool.map(download, resorts))
    for r in resorts:
        print(r["name"])
        df, normal = seasons(r, snowfall(r), enso)
        chart(r, df, normal, OUT / f"{slug(r['name'])}.png")
        strong = df[df.complete & (df.oni >= 1.5)]
        cur = df[~df.complete]
        stats.append(dict(name=r["name"], label=f"{r['name']} ({r['country']})", country=r["country"],
                          region=region(r), access=r["access"], normal_in=normal, png=f"{slug(r['name'])}.png",
                          strong_above=int((strong.pct > 0).sum()), strong_n=len(strong),
                          strong_median=strong.pct.median(),
                          to_date=cur.pct.iloc[-1] if len(cur) else float("nan"), **fit(df, x_new)))
    pd.DataFrame(stats).drop(columns="label").round(3).to_csv(OUT / "forecast.csv", index=False)
    summary([s for s in stats if s["region"] != "Southern Hemisphere"], x_new, OUT / "summary.png")


if __name__ == "__main__":
    main()
