"""D. 量能族群。所有量能指標都以「量的移動平均」正規化,避免股票間不可比。"""
import numpy as np
import pandas as pd

from ._util import W, ema, wilder, rolling_slope, safe_div


def volume_features(df: pd.DataFrame) -> dict:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    r = c.pct_change()
    f = {}
    for w in W:
        f[f"vol_sma_ratio_{w}"] = safe_div(v, v.rolling(w).mean()) - 1
    for w in [20, 60]:
        f[f"vol_z_{w}"] = safe_div(v - v.rolling(w).mean(), v.rolling(w).std())
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    for w in [10, 20, 60]:
        f[f"obv_slope_{w}"] = safe_div(rolling_slope(obv, w), v.rolling(w).mean())
    mfm = safe_div((c - l) - (h - c), h - l).fillna(0)
    f["cmf_20"] = safe_div((mfm * v).rolling(20).sum(), v.rolling(20).sum())
    tp = (h + l + c) / 3
    rmf = tp * v
    dtp = tp.diff()
    pos = rmf.where(dtp > 0, 0.0).rolling(14).sum()
    neg = rmf.where(dtp < 0, 0.0).rolling(14).sum()
    f["mfi_14"] = 100 - 100 / (1 + safe_div(pos, neg))
    ad = (mfm * v).cumsum()
    for w in [10, 20]:
        f[f"ad_roc_{w}"] = safe_div(ad - ad.shift(w), v.rolling(w).sum())
    f["force_13"] = safe_div(ema(c.diff() * v, 13), c * v.rolling(20).mean())
    dm = (h + l) / 2 - (h.shift(1) + l.shift(1)) / 2
    box = safe_div(v / v.rolling(20).mean(), h - l)
    f["eom_14"] = safe_div(dm, box).rolling(14).mean() / c
    for w in [5, 20]:
        vwap = safe_div((tp * v).rolling(w).sum(), v.rolling(w).sum())
        f[f"vwap_ratio_{w}"] = c / vwap - 1
    pvt = (r * v).cumsum()
    for w in [10, 20]:
        f[f"pvt_roc_{w}"] = safe_div(pvt - pvt.shift(w), v.rolling(w).sum())
    dv = v.pct_change()
    for w in [20, 60]:
        f[f"pv_corr_{w}"] = r.rolling(w).corr(dv)
    amt = np.log((c * v).replace(0, np.nan))
    for w in [20, 60]:
        f[f"turnover_z_{w}"] = safe_div(amt - amt.rolling(w).mean(), amt.rolling(w).std())
    roc20 = c / c.shift(20) - 1
    f["pv_diverge_20"] = (np.sign(roc20) != np.sign(f["obv_slope_20"])).astype(float)
    return f
