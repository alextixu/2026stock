"""F. 統計 / 分布族群。"""
import numpy as np
import pandas as pd

from ._util import safe_div


def _hurst(x):
    y = x - x.mean()
    z = np.cumsum(y)
    R = z.max() - z.min()
    S = x.std()
    if S <= 0 or R <= 0:
        return np.nan
    return np.log(R / S) / np.log(len(x))


def _updown_ratio(x):
    up, dn = x[x > 0], -x[x < 0]
    if len(up) == 0 or len(dn) == 0:
        return np.nan
    return up.mean() / dn.mean()


def stats_features(df: pd.DataFrame) -> dict:
    c = df["close"]
    r = c.pct_change()
    f = {}
    for w in [20, 60]:
        f[f"skew_{w}"] = r.rolling(w).skew()
        f[f"kurt_{w}"] = r.rolling(w).kurt()
        f[f"autocorr_{w}"] = r.rolling(w).corr(r.shift(1))
        f[f"ret_z_{w}"] = safe_div(r, r.rolling(w).std())
    for w in [20, 60, 120]:
        f[f"cumret_ex1_{w}"] = c.shift(1) / c.shift(w) - 1
    f["updown_mag_ratio_20"] = r.rolling(20).apply(_updown_ratio, raw=True)
    f["hurst_120"] = r.rolling(120).apply(_hurst, raw=True)
    dow = pd.Series(df.index.dayofweek, index=df.index)
    for d in range(4):
        f[f"dow_{d}"] = (dow == d).astype(float)
    return f
