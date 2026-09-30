"""A. 趨勢族群。所有價格類指標皆轉為相對 close 的比率(尺度不變)。"""
import numpy as np
import pandas as pd

from ._util import W, ema, wilder, wma, true_range, rolling_slope, safe_div


def _adx(h, l, c, n):
    up, dn = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    atr = wilder(true_range(h, l, c), n)
    pdi = 100 * safe_div(wilder(plus_dm, n), atr)
    mdi = 100 * safe_div(wilder(minus_dm, n), atr)
    dx = 100 * safe_div((pdi - mdi).abs(), pdi + mdi)
    return wilder(dx, n), pdi, mdi


def _aroon(h, l, n):
    up = h.rolling(n + 1).apply(lambda x: x.argmax(), raw=True) * 100.0 / n
    dn = l.rolling(n + 1).apply(lambda x: x.argmin(), raw=True) * 100.0 / n
    return up, dn


def _psar(h, l, c, af0=0.02, af_max=0.2):
    n = len(c)
    sar = np.full(n, np.nan)
    hv, lv, cv = h.values, l.values, c.values
    if n < 3:
        return pd.Series(sar, index=c.index)
    bull = cv[1] > cv[0]
    ep = hv[0] if bull else lv[0]
    s = lv[0] if bull else hv[0]
    af = af0
    for i in range(1, n):
        s = s + af * (ep - s)
        if bull:
            s = min(s, lv[i - 1], lv[i - 2] if i >= 2 else lv[i - 1])
            if lv[i] < s:
                bull, s, ep, af = False, ep, lv[i], af0
            elif hv[i] > ep:
                ep, af = hv[i], min(af + af0, af_max)
        else:
            s = max(s, hv[i - 1], hv[i - 2] if i >= 2 else hv[i - 1])
            if hv[i] > s:
                bull, s, ep, af = True, ep, hv[i], af0
            elif lv[i] < ep:
                ep, af = lv[i], min(af + af0, af_max)
        sar[i] = s
    return pd.Series(sar, index=c.index)


def trend_features(df: pd.DataFrame) -> dict:
    c, h, l = df["close"], df["high"], df["low"]
    f = {}
    for w in W:
        f[f"sma_ratio_{w}"] = c / c.rolling(w).mean() - 1
        f[f"ema_ratio_{w}"] = c / ema(c, w) - 1
        f[f"wma_ratio_{w}"] = c / wma(c, w) - 1
    for s, lg in [(5, 20), (10, 60), (20, 120)]:
        f[f"sma_cross_{s}_{lg}"] = c.rolling(s).mean() / c.rolling(lg).mean() - 1
    for a, b, sg in [(12, 26, 9), (5, 35, 5)]:
        diff = ema(c, a) - ema(c, b)
        sig = ema(diff, sg)
        f[f"macd_diff_{a}_{b}_{sg}"] = diff / c
        f[f"macd_signal_{a}_{b}_{sg}"] = sig / c
        f[f"macd_hist_{a}_{b}_{sg}"] = (diff - sig) / c
    for n in [14, 28]:
        adx, pdi, mdi = _adx(h, l, c, n)
        f[f"adx_{n}"], f[f"pdi_{n}"], f[f"mdi_{n}"] = adx, pdi, mdi
    for n in [14, 25]:
        up, dn = _aroon(h, l, n)
        f[f"aroon_up_{n}"], f[f"aroon_dn_{n}"] = up, dn
        f[f"aroon_osc_{n}"] = up - dn
    for w in W:
        f[f"linreg_slope_{w}"] = rolling_slope(c, w) / c
    e3 = ema(ema(ema(c, 15), 15), 15)
    f["trix_15"] = e3.pct_change() * 100
    tr = true_range(h, l, c)
    f["vortex_pos_14"] = safe_div((h - l.shift(1)).abs().rolling(14).sum(), tr.rolling(14).sum())
    f["vortex_neg_14"] = safe_div((l - h.shift(1)).abs().rolling(14).sum(), tr.rolling(14).sum())
    f["psar_dist"] = (c - _psar(h, l, c)) / c
    return f
