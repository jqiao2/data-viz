import numpy as np
import pandas as pd

import comfy_weather as c


def days(**cols):
    return pd.DataFrame(cols, index=pd.date_range("2020-01-01", periods=len(cols["tmax"])))


def test_comfy():
    d = days(tmax=[25, 69.9, 70, 60, 60, 24.9, 60, 30],
             dmax=[40, 54.9, 40, 55, 40, 40, 40, 20],
             cloud=[10, 59, 10, 10, 60, 10, 10, 10],
             rain=[0, 0.03, 0, 0, 0, 0, 0.04, 0],
             snow=[0, 0, 0, 0, 0, 0, 0, 5])
    assert c.comfy(d).tolist() == [True, True, False, False, False, False, False, True]  # snow is fine by default
    assert c.per_month(d)[0] == 3 and sum(c.per_month(d)) == 3


def test_hist_roundtrip():
    """Unpacking the page's histogram and recounting with the default thresholds matches comfy()."""
    rng = np.random.default_rng(1)
    n = 3000
    d = days(tmax=rng.integers(-20, 110, n).astype(float), dmax=rng.integers(-20, 80, n).astype(float),
             cloud=rng.integers(0, 101, n).astype(float), rain=rng.choice([0, 0.02, 0.05, 0.3], n),
             snow=rng.choice([0, 0.5, 2, 8], n))
    e = c.pack(c.hist(d))
    idx, k = np.cumsum(e >> 12), e & 4095
    b = c.BINS
    nr, ns = len(b["rain"]) + 1, len(b["snow"]) + 1
    si, j = idx % ns, idx // ns
    ri, j = j % nr, j // nr
    ci, j = j % b["nc"], j // b["nc"]
    di, ti = j % b["nd"], j // b["nd"]
    ok = ((ti >= (c.HIGH_F[0] - b["t0"]) / b["ts"]) & (ti < (c.HIGH_F[1] - b["t0"]) / b["ts"])
          & (di < (c.DEW_MAX_F - b["d0"]) / b["ds"]) & (ci < c.CLOUD_MAX / b["cs"])
          & (ri < np.searchsorted(b["rain"], c.RAIN_MAX_IN, side="right")))
    assert k[ok].sum() == c.comfy(d).sum() and k.sum() == n


def test_dew():
    assert abs(c.dew_c(611.2)) < 1e-9 and abs(c.dew_c(2339) - 20) < 0.2  # 2339 Pa saturates at 20 °C


def test_world():
    """Plate carree corners land on the canvas corners, summed histograms keep every day even past the 12-bit
    count, the split world_hist files decode back to each cell's packed histogram, and the DEM is north-up."""
    import base64, gzip, json, tempfile
    from pathlib import Path
    from shapely.affinity import affine_transform
    from shapely.geometry import Point
    import comfy_world as w
    rng0 = np.random.default_rng(3)
    assert affine_transform(Point(-180, 84), w.XY).coords[0] == (0, 0)
    assert affine_transform(Point(180, -60), w.XY).coords[0] == (1440, 576)
    n = 2000
    d = days(tmax=rng0.normal(60, 30, n), dmax=rng0.normal(40, 20, n), cloud=rng0.uniform(0, 100, n),
             rain=rng0.choice([0, 0.01, 0.05, 0.3], n), snow=rng0.choice([0, 0.5, 2, 8], n))
    d.iloc[::7, 2] = np.nan  # polar night cloud
    d.iloc[::11, 0] = np.nan
    flat = w.bins(*(d[k].to_numpy() for k in ["tmax", "dmax", "cloud", "rain", "snow"]))
    ref = c.hist(d)
    assert (np.unique(flat[flat >= 0]) == ref[0]).all() and (flat >= 0).sum() == ref[1].sum()
    big = (np.array([5, 9]), np.array([3000, 7]))
    m = w.merge([big, big, (np.array([9]), np.array([1]))])
    assert (m[1] <= 4095).all() and m[1][m[0] == 5].sum() == 6000 and m[1][m[0] == 9].sum() == 15
    rng = np.random.default_rng(2)
    hs = [c.hist(days(tmax=rng.integers(0, 100, n).astype(float), dmax=rng.integers(0, 70, n).astype(float),
                      cloud=rng.integers(0, 101, n).astype(float), rain=np.zeros(n), snow=np.zeros(n)))
          for n in (50, 400, 3000)] + [m]
    with tempfile.TemporaryDirectory() as d:
        w.MAX_FILE, old = 2000, w.MAX_FILE  # force a split
        names, sizes = w.write_hists(Path(d), hs)
        w.MAX_FILE = old
        e = np.concatenate([np.frombuffer(gzip.decompress(base64.b64decode((Path(d) / f).read_bytes())), "<u4")
                            for f in names])
    assert len(names) > 1 and len(e) == sum(sizes)
    assert (np.split(e, np.cumsum(sizes)[:-1])[3] == c.pack(m)).all()
    j = json.loads((c.OUT / "web" / "world.json").read_text(encoding="utf-8"))
    z = np.frombuffer(gzip.decompress(base64.b64decode(j["dem"])), "<i2").reshape(576, 1440)
    px = lambda lat, lon: z[int((84 - lat) * 4), int((lon + 180) * 4)]
    assert px(27.99, 86.93) > 4500 and px(0, -150) == w.NODATA and px(31.5, 35.5) < px(31.95, 35.93)  # Everest; mid-Pacific; Dead Sea below Amman
    assert len(j["cells"]) == len(j["ce"]) == len(j["sizes"]) and j["lapse"]["t"] < 0 and j["lapse"]["d"] < 0


if __name__ == "__main__":
    test_comfy()
    test_hist_roundtrip()
    test_dew()
    test_world()
    print("ok")
