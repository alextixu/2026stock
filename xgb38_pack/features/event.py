"""I. 事件族群:爆量、漲跌停、突破新高新低、連續漲跌、大波動。台股特有的離散事件,原本的連續指標沒有明確表達。"""
import numpy as np
import pandas as pd

from ._util import safe_div


def event_features(df: pd.DataFrame) -> dict:
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    r = c.pct_change()
    pc = c.shift(1)
    avg20 = v.rolling(20).mean()
    vmax20 = v.shift(1).rolling(20).max()
    body = safe_div(c - o, h - l)
    f = {}

    f["ev_vol_x_avg20"] = safe_div(v, avg20)
    f["ev_vol_vs_max20"] = safe_div(v, vmax20)
    spike2 = (f["ev_vol_x_avg20"] > 2).astype(float)
    spike3 = (f["ev_vol_x_avg20"] > 3).astype(float)
    f["ev_spike2"] = spike2
    f["ev_spike3"] = spike3
    f["ev_spike2_cnt5"] = spike2.rolling(5).sum()
    f["ev_spike2_cnt20"] = spike2.rolling(20).sum()
    f["ev_days_since_spike3"] = _days_since(spike3, cap=60)
    f["ev_spike_up"] = spike2 * (r > 0)
    f["ev_spike_down"] = spike2 * (r < 0)
    f["ev_spike_long_red"] = spike2 * ((r > 0.03) & (body > 0.6))
    f["ev_spike_long_black"] = spike2 * ((r < -0.03) & (body < -0.6))
    f["ev_spike_ret"] = spike2 * r
    f["ev_vol_pctile250"] = v.rolling(250).rank(pct=True)
    amt = c * v
    f["ev_amt_pctile250"] = amt.rolling(250).rank(pct=True)
    f["ev_vol_dryup"] = (f["ev_vol_x_avg20"] < 0.5).astype(float)
    f["ev_vol_dryup_cnt5"] = f["ev_vol_dryup"].rolling(5).sum()

    lim_up = (c >= pc * 1.095).astype(float)
    lim_dn = (c <= pc * 0.905).astype(float)
    f["ev_limit_up"] = lim_up
    f["ev_limit_down"] = lim_dn
    f["ev_limit_up_lock"] = lim_up * (c == h)
    f["ev_limit_down_lock"] = lim_dn * (c == l)
    f["ev_limit_up_cnt20"] = lim_up.rolling(20).sum()
    f["ev_limit_down_cnt20"] = lim_dn.rolling(20).sum()
    f["ev_days_since_limit_up"] = _days_since(lim_up, cap=20)
    f["ev_days_since_limit_down"] = _days_since(lim_dn, cap=20)
    f["ev_touch_limit_up"] = (h >= pc * 1.095).astype(float) * (1 - lim_up)

    for w in [20, 60, 250]:
        hi = c.shift(1).rolling(w).max()
        lo = c.shift(1).rolling(w).min()
        f[f"ev_new_high_{w}"] = (c > hi).astype(float)
        f[f"ev_new_low_{w}"] = (c < lo).astype(float)
    f["ev_breakout_vol_60"] = f["ev_new_high_60"] * spike2
    f["ev_breakdown_vol_60"] = f["ev_new_low_60"] * spike2

    f["ev_streak"] = _streak(r)
    f["ev_up_streak_ge3"] = (f["ev_streak"] >= 3).astype(float)
    f["ev_down_streak_ge3"] = (f["ev_streak"] <= -3).astype(float)
    f["ev_bigmove5"] = (r.abs() > 0.05).astype(float)
    f["ev_bigmove5_cnt20"] = f["ev_bigmove5"].rolling(20).sum()
    f["ev_gap_up_spike"] = ((o / pc - 1) > 0.02).astype(float) * spike2
    f["ev_gap_down_spike"] = ((o / pc - 1) < -0.02).astype(float) * spike2
    f["ev_range_x_atr"] = safe_div(h - l, (h - l).rolling(20).mean())
    return f


def _days_since(flag: pd.Series, cap: int) -> pd.Series:
    out = np.full(len(flag), float(cap))
    last = -10 ** 9
    vals = flag.values
    for i in range(len(vals)):
        if vals[i] == 1:
            last = i
        out[i] = min(i - last, cap)
    return pd.Series(out, index=flag.index)


def _streak(r: pd.Series) -> pd.Series:
    s = np.sign(r.fillna(0).values)
    out = np.zeros(len(s))
    for i in range(len(s)):
        if s[i] == 0:
            out[i] = 0
        elif i > 0 and s[i] == s[i - 1]:
            out[i] = out[i - 1] + s[i]
        else:
            out[i] = s[i]
    return pd.Series(out, index=r.index)
