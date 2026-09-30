"""
9 個技術指標 — 嚴格對齊論文 Table 1 (Li et al., KBS 2022).

論文 Grid Search: 指標時間窗口 n ∈ {100, 120, 130, 140}
注意: 論文的 n 與 rolling window 同步調整, 非傳統 TA 常用的 14 日.

MACD 公式 (論文 Table 1):
  DIFF_t = EMA(12)_t − EMA(26)_t
  MACD(n)_t = MACD(n)_{t-1} + 2/(n+1) × (DIFF_t − MACD(n)_{t-1})
  → 等價於 DIFF 的 EMA(n)

Stochastic D% (論文 Table 1): K% 的 n 日移動平均
"""
import numpy as np
import pandas as pd


def compute_indicators(df: pd.DataFrame, n: int = 100,
                       stats: tuple = None, return_stats: bool = False):
    """
    Args:
        df:    DataFrame with columns ['open', 'high', 'low', 'close']
        n:     時間窗口 (論文 grid search: {100, 120, 130, 140})
        stats: (mean, std) — 若提供則用它做 z-score (應來自訓練期,
               避免前視偏誤); None 則用本 df 自身統計量
        return_stats: True 時回傳 (feats, (mean, std)) 供測試集重用

    Returns:
        np.ndarray of shape (T, 9), z-score normalized
        (或 (feats, stats) 若 return_stats=True)
    """
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)

    # 1. Simple n-day MA
    sma = close.rolling(n, min_periods=1).mean()

    # 2. Weighted n-day MA
    weights = np.arange(1, n + 1)
    wma = close.rolling(n, min_periods=1).apply(
        lambda x: np.dot(x[-len(weights):] if len(x) >= n else x,
                         weights[-len(x):]) / weights[-len(x):].sum(),
        raw=True,
    )

    # 3. Momentum (n-day): c_t − c_{t−n+1}
    mom = close - close.shift(n - 1)

    # 4. MACD (論文 Table 1): DIFF = EMA(12)−EMA(26), MACD = EMA(n) of DIFF
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    diff = ema12 - ema26
    macd = diff.ewm(span=n, adjust=False).mean()

    # 5. Larry Williams %R (n-day)
    hh = high.rolling(n, min_periods=1).max()
    ll = low.rolling(n, min_periods=1).min()
    williams_r = (hh - close) / (hh - ll + 1e-9) * 100

    # 6. CCI (n-day)
    typical = (high + low + close) / 3
    sm = typical.rolling(n, min_periods=1).mean()
    md = typical.rolling(n, min_periods=1).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    cci = (typical - sm) / (0.015 * md + 1e-9)

    # 7. Stochastic K% (n-day)
    k = (close - ll) / (hh - ll + 1e-9) * 100

    # 8. Stochastic D% (n-day MA of K%)
    d = k.rolling(n, min_periods=1).mean()

    # 9. RSI (n-day)
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n, min_periods=1).mean()
    dw = (-delta).clip(lower=0).rolling(n, min_periods=1).mean()
    rs = up / (dw + 1e-9)
    rsi = 100 - 100 / (1 + rs)

    feats = np.stack(
        [sma, wma, mom, macd, williams_r, cci, k, d, rsi], axis=1
    )

    # fill NaN / inf
    feats = np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)

    # z-score per column — 統計量可外部注入 (訓練期), 避免前視偏誤
    if stats is None:
        mean = feats.mean(axis=0, keepdims=True)
        std = feats.std(axis=0, keepdims=True) + 1e-9
    else:
        mean, std = stats
    feats = ((feats - mean) / std).astype(np.float32)

    if return_stats:
        return feats, (mean, std)
    return feats


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    n_days = 300
    close = 100 + np.cumsum(rng.normal(0, 1, n_days))
    df = pd.DataFrame({
        'open': close + rng.normal(0, 0.3, n_days),
        'high': close + np.abs(rng.normal(0, 0.5, n_days)),
        'low': close - np.abs(rng.normal(0, 0.5, n_days)),
        'close': close,
    })

    for n in [14, 100, 120]:
        feats = compute_indicators(df, n=n)
        print(f"n={n}: shape={feats.shape}, mean={feats.mean():.3f}, std={feats.std():.3f}")
