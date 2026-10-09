"""~10 km grid maps of sheep density and sheep per 100 people in Europe, 2015.

Sheep: FAO Gridded Livestock of the World (GLW4). Population: JRC GHS-POP.
Run: python sheep_grid_maps.py  -> downloads raw data to data/raw/, writes PNGs to output/.
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import LogNorm
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds

from sheep_maps import BBOX, OUT, RAW, SOURCES as NUTS_SOURCES, download

POP_TIF = "GHS_POP_E2015_GLOBE_R2023A_4326_30ss_V1_0.tif"
SOURCES = {
    "glw4_sheep_2015_da.tif": "https://dataverse.harvard.edu/api/access/datafile/6769626",  # sheep per pixel
    "glw4_area_km2.tif": "https://dataverse.harvard.edu/api/access/datafile/6769631",  # km² per pixel
    "ghs_pop_2015_30ss.zip": "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
    "GHS_POP_E2015_GLOBE_R2023A_4326_30ss/V1-0/GHS_POP_E2015_GLOBE_R2023A_4326_30ss_V1_0.zip",
    "nuts_2024.geojson": NUTS_SOURCES["nuts_2024.geojson"],
    "nuts_2016.geojson": NUTS_SOURCES["nuts_2016.geojson"],
}
LONLAT = (-25, 33, 46, 72)  # Europe window in degrees (W, S, E, N)


def read_window(path):
    with rasterio.open(path) as src:
        w = from_bounds(*LONLAT, src.transform).round_offsets().round_lengths()
        return src.read(1, window=w, masked=True).filled(0).astype("float64"), src.window_transform(w)


def build():
    sheep, transform = read_window(RAW / "glw4_sheep_2015_da.tif")
    area, _ = read_window(RAW / "glw4_area_km2.tif")

    # Sum the 30" population pixels into the 5' sheep cells (the two grids are offset by ~1.5").
    pop = np.zeros_like(sheep)
    with rasterio.open(f"zip://{(RAW / 'ghs_pop_2015_30ss.zip').as_posix()}!{POP_TIF}") as src:
        w = from_bounds(*LONLAT, src.transform).round_offsets().round_lengths()
        reproject(src.read(1, window=w), pop, src_transform=src.window_transform(w), src_crs=src.crs,
                  dst_transform=transform, dst_crs="EPSG:4326", resampling=Resampling.sum)

    # Keep only NUTS countries (EU, EFTA, candidates, UK); drops North Africa, Russia, Belarus...
    nuts = gpd.read_file(RAW / "nuts_2024.geojson")
    uk = gpd.read_file(RAW / "nuts_2016.geojson")
    countries = pd.concat([nuts[nuts["LEVL_CODE"] == 0], uk[uk["NUTS_ID"] == "UK"]]).to_crs(4326)
    outside = geometry_mask(countries.geometry, sheep.shape, transform)

    density = np.where(outside, np.nan, sheep / area)
    # ponytail: floor population at 1 person so empty upland cells don't divide by zero;
    # those cells read as "sheep per 100 people" of an imaginary single resident.
    per_100 = np.where(outside, np.nan, sheep / np.maximum(pop, 1) * 100)
    return density, per_100, transform, countries, pop[~outside].sum(), sheep[~outside].sum()


def to_laea(arr, transform):
    """Reproject a lon/lat grid onto a 5 km EPSG:3035 grid covering BBOX, to match the NUTS maps."""
    w, h = (BBOX[2] - BBOX[0]) // 5000, (BBOX[3] - BBOX[1]) // 5000
    out = np.full((h, w), np.nan)
    reproject(arr, out, src_transform=transform, src_crs="EPSG:4326", dst_transform=transform_from_bounds(*BBOX, w, h),
              dst_crs="EPSG:3035", resampling=Resampling.nearest, src_nodata=np.nan, dst_nodata=np.nan)
    return out


def plot(arr, countries, title, label, filename):
    fig, ax = plt.subplots(figsize=(10, 9))
    countries.plot(ax=ax, color="#e0e0e0", edgecolor="none")  # grey = no sheep
    data = np.where(arr > 0, arr, np.nan)
    im = ax.imshow(data, extent=(BBOX[0], BBOX[2], BBOX[1], BBOX[3]), cmap="Greens", interpolation="nearest", zorder=2,
                   norm=LogNorm(np.nanpercentile(data, 5), np.nanpercentile(data, 99.5)))
    countries.boundary.plot(ax=ax, color="white", linewidth=0.4, zorder=3)
    fig.colorbar(im, ax=ax, label=label, shrink=0.6, extend="both")
    ax.set_xlim(BBOX[0], BBOX[2])
    ax.set_ylim(BBOX[1], BBOX[3])
    ax.set_axis_off()
    ax.set_title(title, loc="left", fontsize=14)
    ax.text(0, 0, "Sources: FAO GLW4 sheep 2015 (Gilbert et al. 2022); JRC GHS-POP 2015 R2023A; "
            "boundaries © EuroGeographics. Grey = no sheep.", transform=ax.transAxes, fontsize=8, color="#555")
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / filename, dpi=150, bbox_inches="tight")
    print(f"wrote {OUT / filename}")


if __name__ == "__main__":
    download(SOURCES)
    density, per_100, transform, countries, total_pop, total_sheep = build()
    print(f"in-map totals: {total_pop / 1e6:.0f}M people, {total_sheep / 1e6:.0f}M sheep")
    # Sanity check: NUTS countries hold roughly 600-700M people (incl. Turkey, Ukraine) and ~100-180M sheep.
    assert 5e8 < total_pop < 8e8 and 5e7 < total_sheep < 2.5e8, (total_pop, total_sheep)
    countries = countries.to_crs(3035)
    plot(to_laea(density, transform), countries, "Sheep per km² in Europe, 2015 (~10 km grid)",
         "sheep per km² (log scale)", "sheep_density_grid.png")
    plot(to_laea(per_100, transform), countries, "Sheep per 100 people in Europe, 2015 (~10 km grid)",
         "sheep per 100 people (log scale)", "sheep_per_100_people_grid.png")
