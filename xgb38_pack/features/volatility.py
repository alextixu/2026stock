"""C. 波動族群。"""
import numpy as np
import pandas as pd

from ._util import W, ema, wilder, true_range, safe_div


def _mdd(x):
    return (x / np.maximum.accumulate(x) - 1).min()


def volatility_features(df: pd.DataFrame) -> dict:
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    r = c.pct_change()
    lhl = np.log(h / l)
    lco = np.log(c / o)
    f = {}
    tr = true_range(h, l, c)
    for n in [14, 28]:
        f[f"atr_ratio_{n}"] = wilder(tr, n) / c
    for w in W:
        f[f"ret_std_{w}"] = r.rolling(w).std()
        f[f"parkinson_{w}"] = np.sqrt((lhl ** 2).rolling(w).mean() / (4 * np.log(2)))
        gk = 0.5 * lhl ** 2 - (2 * np.log(2) - 1) * lco ** 2
        f[f"gk_vol_{w}"] = np.sqrt(gk.rolling(w).mean().clip(lower=0))
    for n in [20, 60]:
        mid, sd = c.rolling(n).mean(), c.rolling(n).std()
        f[f"bb_pctb_{n}"] = safe_div(c - (mid - 2 * sd), 4 * sd)
        f[f"bb_bw_{n}"] = 4 * sd / mid
    f["keltner_pos_20_10"] = safe_div(c - ema(c, 20), 2 * wilder(tr, 10))
    for w in W:
        hh, ll = h.rolling(w).max(), l.rolling(w).min()
        f[f"donchian_pos_{w}"] = safe_div(c - ll, hh - ll)
    for w in [20, 60, 120]:
        f[f"dist_high_{w}"] = c / h.rolling(w).max() - 1
        f[f"dist_low_{w}"] = c / l.rolling(w).min() - 1
    for w in [20, 60]:
        f[f"mdd_{w}"] = c.rolling(w).apply(_mdd, raw=True)
    f["vol_ratio_5_60"] = safe_div(f["ret_std_5"], f["ret_std_60"])
    dd = (c / c.rolling(14).max() - 1) * 100
    f["ulcer_14"] = np.sqrt((dd ** 2).rolling(14).mean())
    return f
