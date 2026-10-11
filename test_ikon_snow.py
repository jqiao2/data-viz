import numpy as np
import pandas as pd

import ikon_snow as k


def test_fit():
    x = np.linspace(-2, 2.5, 60)
    y = 10 + 20 * x + np.tile([-5.0, 5.0], 30)  # known line plus symmetric noise
    f = k.fit(pd.DataFrame(dict(oni=x, pct=y, complete=True)), 2.0)
    assert abs(f["slope"] - 20) < 0.5 and abs(f["pred"] - 50) < 1
    assert 0.99 < f["p_above"] <= 1 and f["r"] > 0.95


def test_snow_fraction():
    assert k.snow_fraction(pd.Series([-5, 0.5, 1.25, 2, 5.0])).tolist() == [1, 1, 0.5, 0, 0]


if __name__ == "__main__":
    test_fit()
    test_snow_fraction()
    print("ok")
