"""E. K 棒結構族群。"""
import pandas as pd

from ._util import safe_div


def candle_features(df: pd.DataFrame) -> dict:
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    rng = h - l
    base = {
        "body_range": safe_div(c - o, rng),
        "upper_shadow": safe_div(h - pd.concat([c, o], axis=1).max(axis=1), rng),
        "lower_shadow": safe_div(pd.concat([c, o], axis=1).min(axis=1) - l, rng),
        "close_pos": safe_div(c - l, rng),
        "gap": o / c.shift(1) - 1,
        "range_ratio": rng / c,
    }
    f = dict(base)
    for k, s in base.items():
        for w in [5, 20]:
            f[f"{k}_ma{w}"] = s.rolling(w).mean()
    return f
