import numpy as np
import pandas as pd

W = [5, 10, 20, 60, 120]
EPS = 1e-12


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def wma(s: pd.Series, w: int) -> pd.Series:
    k = np.arange(1, w + 1, dtype=float)
    k /= k.sum()
    return s.rolling(w).apply(lambda x: np.dot(x, k), raw=True)


def true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def rolling_slope(s: pd.Series, w: int) -> pd.Series:
    """過去 w 日對時間的 OLS 斜率(每日變化量)。"""
    t = np.arange(w, dtype=float)
    t -= t.mean()
    denom = (t ** 2).sum()
    return s.rolling(w).apply(lambda x: np.dot(t, x) / denom, raw=True)


def safe_div(a, b):
    return a / (b.replace(0, np.nan) if isinstance(b, pd.Series) else (b if b != 0 else np.nan))
