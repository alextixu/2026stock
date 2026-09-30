"""特徵建構總入口:單股七族 + 橫斷面 + 論文 9 指標(E0 對照用)。"""
import numpy as np
import pandas as pd

from .trend import trend_features
from .momentum import momentum_features
from .volatility import volatility_features
from .volume import volume_features
from .candle import candle_features
from .stats import stats_features
from .event import event_features
from .cross_section import cross_section_features
from .chip import chip_features, CHIP_COLUMNS
from .lookback import apply_lookback_mask, required_rows

FAMILY_FUNCS = [
    ("trend", trend_features),
    ("momentum", momentum_features),
    ("volatility", volatility_features),
    ("volume", volume_features),
    ("candle", candle_features),
    ("stats", stats_features),
    ("event", event_features),
]

CLASSIC_SETS = {
    "ma": ["sma_ratio_5", "sma_ratio_20", "sma_ratio_60", "sma_ratio_120",
           "sma_cross_5_20", "sma_cross_10_60", "sma_cross_20_120"],
    "macd": ["macd_diff_12_26_9", "macd_signal_12_26_9", "macd_hist_12_26_9"],
    "kd": ["stoch_k_14_3", "stoch_d_14_3"],
    "rsi": ["rsi_6", "rsi_14", "rsi_28"],
}
CLASSIC_SETS["classic4"] = sum(CLASSIC_SETS.values(), [])


def single_stock_features(df: pd.DataFrame):
    """回傳 (DataFrame(T, F), {name: family})。"""
    cols, fam = {}, {}
    for family, fn in FAMILY_FUNCS:
        for k, s in fn(df).items():
            cols[k] = s
            fam[k] = family
    out = pd.DataFrame(cols, index=df.index)
    return out, fam


def build_panel(stock_data: dict, paper9_n: int = 140, verbose: bool = True,
                chip_data: dict = None, lookback_mask: bool = False):
    """所有股票 → panel (MultiIndex date, ticker),含 OHLCV、單股特徵、橫斷面特徵、paper9。

    記憶體:全部以 float32 numpy 區塊組裝,只在最後建一次 DataFrame(本機 commit 上限很低)。
    """
    from ..data import paper9_indicators, PAPER9_NAMES
    blocks, idxs, cols, fam = [], [], None, {}
    for i, (tk, df) in enumerate(stock_data.items()):
        feats, fam = single_stock_features(df)
        if lookback_mask:
            feats = apply_lookback_mask(feats)
        p9 = paper9_indicators(df, n=paper9_n, stats=(0.0, 1.0)).astype(np.float32)
        arrs = [df.values.astype(np.float32), feats.values.astype(np.float32), p9]
        if chip_data is not None:
            ch = chip_features(df, chip_data.get(tk, {})).shift(1)
            arrs.append(ch.values.astype(np.float32))
        if cols is None:
            cols = list(df.columns) + list(feats.columns) + PAPER9_NAMES
            if chip_data is not None:
                cols += list(CHIP_COLUMNS)
        blocks.append(np.hstack(arrs))
        idxs.append(pd.MultiIndex.from_arrays(
            [df.index, np.repeat(tk, len(df))], names=["date", "ticker"]))
        del feats, p9
        if verbose and (i + 1) % 25 == 0:
            print(f"  特徵 {i + 1}/{len(stock_data)}")
    X = np.vstack(blocks)
    del blocks
    index = idxs[0].append(idxs[1:])
    order = np.lexsort((index.get_level_values(1), index.get_level_values(0)))
    panel = pd.DataFrame(X[order], index=index[order], columns=cols)
    del X
    for k in PAPER9_NAMES:
        fam[k] = "paper9"
    if chip_data is not None:
        for k in CHIP_COLUMNS:
            fam[k] = "chip"
    cs = cross_section_features(panel).astype(np.float32)
    for k in cs.columns:
        fam[k] = "cross_section"
    panel = pd.concat([panel, cs], axis=1)
    del cs
    panel = panel.replace([np.inf, -np.inf], np.nan)
    return panel, fam
