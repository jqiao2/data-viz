"""Maps of sheep density and sheep per 100 people across Europe (Eurostat data).

Run: python sheep_maps.py  -> downloads raw data to data/raw/, writes PNGs to output/.
See README.md for the data sources.
"""
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import requests
from matplotlib.colors import LogNorm

RAW = Path("data/raw")
OUT = Path("output")
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{}?format=TSV&compressed=false"
SOURCES = {
    "apro_mt_ls_r.tsv": EUROSTAT.format("apro_mt_ls_r"),      # animal populations by NUTS 2 region
    # farm structure survey, sheep heads by NUTS 2; filtered server-side (full table is ~120 MB)
    "ef_lsk_sheep.tsv": EUROSTAT.format("ef_lsk_sheep/A.TOTAL.TOTAL.TOTAL.TOTAL.A4100.HD."),
    "apro_mt_lssheep.tsv": EUROSTAT.format("apro_mt_lssheep"),  # national sheep population
    "demo_r_d2jan.tsv": EUROSTAT.format("demo_r_d2jan"),      # population on 1 January by NUTS 2 region
    "nuts_2024.geojson": "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_20M_2024_3035.geojson",
    # UK left NUTS after Brexit; 2016 is the last version with UK regions.
    "nuts_2016.geojson": "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_20M_2016_3035.geojson",
}
MIN_YEAR = 2015
# Mainland Europe + islands in EPSG:3035 metres; drops Canaries, Azores, Madeira, French overseas.
BBOX = (2_400_000, 1_350_000, 7_800_000, 5_500_000)


def download():
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = RAW / name
        if not path.exists():
            print(f"downloading {url}")
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            path.write_bytes(r.content)


def latest(path, **filters):
    """Read a Eurostat TSV and return {geo: latest value since MIN_YEAR} for rows matching filters."""
    df = pd.read_csv(path, sep="\t", dtype=str)
    key = df.columns[0]
    dims = key.split("\\")[0].split(",")
    df[dims] = df[key].str.split(",", expand=True)
    for dim, val in filters.items():
        df = df[df[dim] == val]
    df.columns = [c.strip() for c in df.columns]
    years = [c for c in df.columns if c.isdigit() and int(c) >= MIN_YEAR]
    # Values look like "123.4 e" (flagged) or ":" (missing).
    vals = df.set_index("geo")[years].apply(lambda s: pd.to_numeric(s.str.extract(r"([\d.]+)")[0]))
    return vals.ffill(axis=1).iloc[:, -1].dropna()


def build():
    sheep = latest(RAW / "apro_mt_ls_r.tsv", animals="A4100", unit="THS_HD") * 1000
    farm = latest(RAW / "ef_lsk_sheep.tsv")
    national = latest(RAW / "apro_mt_lssheep.tsv", month="M11_M12", animals="A4100", unit="THS_HD") * 1000
    # Source priority: regional livestock survey > farm structure survey > national total.
    for fallback in (farm, national):
        sheep = pd.concat([sheep, fallback[~fallback.index.isin(sheep.index)]])
    pop = latest(RAW / "demo_r_d2jan.tsv", unit="NR", sex="T", age="TOTAL")

    nuts = gpd.read_file(RAW / "nuts_2024.geojson")
    uk = gpd.read_file(RAW / "nuts_2016.geojson")
    nuts = pd.concat([nuts, uk[uk["CNTR_CODE"] == "UK"]], ignore_index=True)
    c = nuts.geometry.centroid
    nuts = nuts[c.x.between(BBOX[0], BBOX[2]) & c.y.between(BBOX[1], BBOX[3])].copy()
    nuts["sheep"] = nuts["NUTS_ID"].map(sheep)
    nuts["pop"] = nuts["NUTS_ID"].map(pop)

    nuts = nuts.set_index("NUTS_ID", drop=False)
    ok = nuts[["sheep", "pop"]].notna().all(axis=1)

    def finest(nid, level):
        """IDs covering region nid at the finest detail where every piece has data, or None."""
        kids = nuts.index[(nuts["LEVL_CODE"] == level + 1) & nuts.index.str.startswith(nid)]
        picks = [finest(k, level + 1) for k in kids]
        if len(kids) and all(picks):
            return sum(picks, [])
        return [nid] if ok[nid] else None

    # Countries with no usable data at any level are drawn grey at NUTS 0.
    ids = sum((finest(c, 0) or [c] for c in nuts.index[nuts["LEVL_CODE"] == 0]), [])
    gdf = nuts.loc[ids].reset_index(drop=True)
    gdf["sheep_per_km2"] = gdf["sheep"] / (gdf.area / 1e6)  # EPSG:3035 is equal-area
    gdf["sheep_per_100_people"] = gdf["sheep"] / gdf["pop"] * 100
    return gdf


def plot(gdf, column, title, label, filename):
    fig, ax = plt.subplots(figsize=(10, 9))
    data = gdf[gdf[column] > 0]
    gdf.plot(ax=ax, color="#e0e0e0", edgecolor="white", linewidth=0.3)  # no-data base layer
    data.plot(ax=ax, column=column, cmap="Greens", edgecolor="white", linewidth=0.3,
              norm=LogNorm(data[column].quantile(0.02), data[column].max()),
              legend=True, legend_kwds={"label": label, "shrink": 0.6})
    ax.set_xlim(BBOX[0], BBOX[2])
    ax.set_ylim(BBOX[1], BBOX[3])
    ax.set_axis_off()
    ax.set_title(title, loc="left", fontsize=14)
    ax.text(0, 0, f"Source: Eurostat (apro_mt_ls_r, ef_lsk_sheep, apro_mt_lssheep, demo_r_d2jan), latest year {MIN_YEAR}+; "
             "boundaries © EuroGeographics. Grey = no data.", transform=ax.transAxes, fontsize=8, color="#555")
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / filename, dpi=150, bbox_inches="tight")
    print(f"wrote {OUT / filename}")


if __name__ == "__main__":
    download()
    gdf = build()
    assert (gdf["sheep_per_km2"].dropna() >= 0).all() and gdf["NUTS_ID"].is_unique
    plot(gdf, "sheep_per_km2", "Sheep per km² in Europe", "sheep per km² (log scale)", "sheep_density.png")
    plot(gdf, "sheep_per_100_people", "Sheep per 100 people in Europe",
         "sheep per 100 people (log scale)", "sheep_per_100_people.png")
