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
LONG = 30  # seasons a record needs before its own El Niño fit is trusted
# NOAA COOP stations with long snowfall records near a resort: (resort slug, ACIS station id, station name).
# Chosen as the highest long record within ~50 km; several sit well below the slopes or across a divide.
STATIONS = [
    ("big-bear-mountain-resort", "040741 2", "Big Bear Lake"), ("palisades-tahoe", "048758 2", "Tahoe City"),
    ("taos-ski-valley", "292700 2", "Eagle Nest"), ("crystal-mountain", "456898 2", "Rainier Paradise"),
    ("the-summit-at-snoqualmie", "452384 2", "Easton"), ("mt-bachelor", "356252 2", "Odell Lake East"),
    ("mammoth-mountain", "045280 2", "Mammoth Lakes RS"), ("june-mountain", "044881 2", "Lee Vining"),
    ("sierra-at-tahoe", "93230 1", "South Lake Tahoe AP"), ("snowshoe", "468308 2", "Snowshoe"),
    ("killington-pico", "431433 2", "Chittenden"), ("sugarbush-resort", "435416 2", "Mount Mansfield"),
    ("stratton", "436335 2", "Peru"), ("loon-mountain", "276818 2", "Pinkham Notch"),
    ("sunday-river", "276818 2", "Pinkham Notch"), ("sugarloaf", "177037 2", "Rangeley"),
    ("schweitzer", "108137 2", "Sandpoint"), ("alyeska-resort", "500243 2", "Alyeska"),
    ("blue-mountain-resort", "364934 2", "Lehighton"), ("camelback-resort", "368596 2", "East Stroudsburg"),
]
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
    oni_now = k.latest_oni(enso)
    for slug, sid, name in STATIONS:
        path = VAL / f"station-{slug}--{name.lower().replace(' ', '-')}.csv"
        if not path.exists():
            station_seasons(sid).to_csv(path, index=False)
    resorts = {k.slug(r["name"]): r for r in csv.DictReader(open(k.RESORTS, encoding="utf-8"))}
    (VAL / "compare").mkdir(exist_ok=True)
    rows = []
    for f in sorted(VAL.glob("*.csv")):
        station = f.stem.startswith("station-")
        slug, _, source = f.stem.removeprefix("station-").partition("--")  # station-<resort>--<station>.csv
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
        # Same line fit as the outlook, once on the observed record and once on the model over the same seasons
        obs_fit = k.fit(pd.DataFrame(dict(oni=m.oni, pct=od * 100, complete=True)), oni_now)
        mod_fit = k.fit(pd.DataFrame(dict(oni=m.oni, pct=pdp * 100, complete=True)), oni_now)
        rows.append(dict(
            resort=r["name"], series=f"station: {source.replace('-', ' ').title()}" if station else "resort report", n=len(m),
            seasons=f"{m.season.iloc[0]}..{m.season.iloc[-1]}", r=o.corr(p), same_side=(od * pdp > 0).mean(),
            obs_mean=o.mean(), model_mean=p.mean(), ratio=o.mean() / p.mean(), strong_n=int(strong.sum()),
            strong_obs_above=int((od[strong] > 0).sum()), strong_model_above=int((pdp[strong] > 0).sum()),
            oni_r_obs=m.oni.corr(o), oni_r_model=m.oni.corr(p), t_obs=obs_fit["t"], p_above_obs=obs_fit["p_above"],
            p_above_model_same_seasons=mod_fit["p_above"]))
        m.assign(obs_dep=od.round(3), model_dep=pdp.round(3))[
            ["season", "snowfall_in", "total", "oni", "obs_dep", "model_dep"]].to_csv(VAL / "compare" / f.name, index=False)
    out = pd.DataFrame(rows).sort_values("r", ascending=False)
    out.round(3).to_csv(VAL / "summary.csv", index=False)
    # Best long record per resort (most closely tracked by the model) for the real-record El Niño odds
    best = out[out.n >= LONG].sort_values("r", ascending=False).drop_duplicates("resort")
    best.round(3).to_csv(VAL / "enso_check.csv", index=False)
    print(f"long records: {len(best)}; ENSO link obs vs model across them r = {best.oni_r_obs.corr(best.oni_r_model):.2f}")
    pd.set_option("display.width", 250)
    print(out.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
