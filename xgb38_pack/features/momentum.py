"""B. 動量族群。"""
import numpy as np
import pandas as pd

from ._util import W, ema, wilder, safe_div


def rsi(c, n):
    d = c.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + safe_div(up, dn))


def stoch(h, l, c, n, sm=3):
    hh, ll = h.rolling(n).max(), l.rolling(n).min()
    fast_k = 100 * safe_div(c - ll, hh - ll)
    k = fast_k.rolling(sm).mean()
    d = k.rolling(sm).mean()
    return k, d


def momentum_features(df: pd.DataFrame) -> dict:
    c, h, l = df["close"], df["high"], df["low"]
    r = c.pct_change()
    f = {}
    for w in W:
        f[f"roc_{w}"] = c / c.shift(w) - 1
    for n in [6, 14, 28]:
        f[f"rsi_{n}"] = rsi(c, n)
    for n in [14, 60]:
        k, d = stoch(h, l, c, n)
        f[f"stoch_k_{n}_3"], f[f"stoch_d_{n}_3"] = k, d
    r14 = f["rsi_14"]
    f["stochrsi_14"] = safe_div(r14 - r14.rolling(14).min(),
                                r14.rolling(14).max() - r14.rolling(14).min())
    for n in [14, 28]:
        hh, ll = h.rolling(n).max(), l.rolling(n).min()
        f[f"willr_{n}"] = -100 * safe_div(hh - c, hh - ll)
    tp = (h + l + c) / 3
    for n in [14, 20, 60]:
        sm = tp.rolling(n).mean()
        md = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        f[f"cci_{n}"] = safe_div(tp - sm, 0.015 * md)
    d = c.diff()
    su, sd = d.clip(lower=0).rolling(14).sum(), (-d).clip(lower=0).rolling(14).sum()
    f["cmo_14"] = 100 * safe_div(su - sd, su + sd)
    f["ppo_12_26"] = 100 * safe_div(ema(c, 12) - ema(c, 26), ema(c, 26))
    f["tsi_25_13"] = 100 * safe_div(ema(ema(d, 25), 13), ema(ema(d.abs(), 25), 13))
    pc = c.shift(1)
    bp = c - pd.concat([l, pc], axis=1).min(axis=1)
    tr = pd.concat([h, pc], axis=1).max(axis=1) - pd.concat([l, pc], axis=1).min(axis=1)
    avg = [safe_div(bp.rolling(n).sum(), tr.rolling(n).sum()) for n in (7, 14, 28)]
    f["uo_7_14_28"] = 100 * (4 * avg[0] + 2 * avg[1] + avg[2]) / 7
    mp = (h + l) / 2
    f["ao_5_34"] = (mp.rolling(5).mean() - mp.rolling(34).mean()) / c
    for k in range(1, 11):
        f[f"ret_lag_{k}"] = r.shift(k - 1)
    for w in W:
        f[f"up_ratio_{w}"] = (r > 0).astype(float).rolling(w).mean()
    f["mom_accel_5_20"] = f["roc_5"] - f["roc_20"]
    return f
