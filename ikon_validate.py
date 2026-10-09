"""Compare observed season snowfall with the ERA5-derived totals in ikon_snow.py.

Run: python ikon_validate.py  -> reads data/validation/<resort>.csv (resort-reported totals) and
data/validation/station-<resort>.csv (NOAA COOP stations via ACIS), writes season-by-season
data/validation/compare/<file>.csv and data/validation/summary.csv. Departures are measured from each series' own
mean over the seasons both cover, because the model's absolute totals run well below resort reports.
"""
import csv
from pathlib import Path

import pandas as pd
import requests

import ikon_snow as k

VAL = Path("data/validation")
# NOAA COOP stations with long snowfall records near a resort: resort slug -> (ACIS station id, name)
STATIONS = {"big-bear-mountain-resort": ("040741 2", "Big Bear Lake"), "palisades-tahoe": ("048758 2", "Tahoe City"),
            "taos-ski-valley": ("292700 2", "Eagle Nest")}
MAX_MISSING = 10  # winter (Nov-Apr) days a season may miss and still count


def station_seasons(sid):
    """July-June snowfall totals (inches) for an ACIS station, skipping seasons with too many missing winter days."""
    r = requests.post("https://data.rcc-acis.org/StnData", timeout=120,
                      json={"sid": sid, "sdate": "1950-07-01", "edate": "2026-06-30", "elems": "snow"})
    r.raise_for_status()
    s = pd.Series({pd.Timestamp(d): v for d, v in r.json()["data"]})
    val = pd.to_numeric(s.replace("T", "0").str.rstrip("AS"), errors="coerce")  # T = trace; A/S = accumulated flags
    g = pd.DataFrame({"v": val, "season": val.index.year - (val.index.month < 7),
                      "winter": val.index.month.isin([11, 12, 1, 2, 3, 4])})
    return pd.DataFrame([(f"{y}-{str(y + 1)[2:]}", round(grp.v.sum(), 1), int(grp[grp.winter].v.isna().sum()))
                         for y, grp in g.groupby("season") if grp[grp.winter].v.isna().sum() <= MAX_MISSING],
                        columns=["season", "snowfall_in", "missing_winter_days"])


def main():
    enso = k.oni()
    for slug, (sid, _) in STATIONS.items():
        path = VAL / f"station-{slug}.csv"
        if not path.exists():
            station_seasons(sid).to_csv(path, index=False)
    resorts = {k.slug(r["name"]): r for r in csv.DictReader(open(k.RESORTS, encoding="utf-8"))}
    (VAL / "compare").mkdir(exist_ok=True)
    rows = []
    for f in sorted(VAL.glob("*.csv")):
        station = f.stem.startswith("station-")
        slug = f.stem.removeprefix("station-")
        if slug not in resorts:
            continue
        r = resorts[slug]
        model, _ = k.seasons(r, k.snowfall(r), enso)
        model["season"] = model.label.str.replace("–", "-")
        m = pd.read_csv(f).drop_duplicates("season").merge(model[model.complete], on="season")
        if len(m) < 5:
            continue
        o, p = m.snowfall_in, m.total
        od, pdp = o / o.mean() - 1, p / p.mean() - 1
        strong = m.oni >= 1.5
        rows.append(dict(
            resort=r["name"], series="NOAA station" if station else "resort report", n=len(m),
            seasons=f"{m.season.iloc[0]}..{m.season.iloc[-1]}", r=o.corr(p), same_side=(od * pdp > 0).mean(),
            obs_mean=o.mean(), model_mean=p.mean(), ratio=o.mean() / p.mean(), strong_n=int(strong.sum()),
            strong_obs_above=int((od[strong] > 0).sum()), strong_model_above=int((pdp[strong] > 0).sum()),
            oni_r_obs=m.oni.corr(o), oni_r_model=m.oni.corr(p)))
        m.assign(obs_dep=od.round(3), model_dep=pdp.round(3))[
            ["season", "snowfall_in", "total", "oni", "obs_dep", "model_dep"]].to_csv(VAL / "compare" / f.name, index=False)
    out = pd.DataFrame(rows).sort_values("r", ascending=False)
    out.round(3).to_csv(VAL / "summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
