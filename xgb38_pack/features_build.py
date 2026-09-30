"""特徵層:整池價格 + 籌碼 → 38 欄模型輸入(MultiIndex date, ticker)。

保留原特徵計算公式與欄位順序，新增套件匯入及資料品質檢查:
    build_panel(lookback_mask=True)  → 248 欄單股 + 橫斷面特徵
    extra_single_stock / extra_cross_section → 65 欄長週期動能、隔夜日內、籌碼
    取 feature_shortlist_v2.csv 的 38 欄,**順序照該檔**

注意:
  - 這是整池計算,不能一檔一檔跑。38 欄裡有 4 欄(cs_*)是當日池內排名/超額,少一檔就不對。
  - 回看最長 200 交易日(mad_21_200),價格至少要 260 根才算得出完整的一列。
  - 籌碼維持原模型的 chip_shift=0；外資持股等發布時點尚未逐項驗證。
"""
import numpy as np
import pandas as pd

from . import chip_data as C
from .config import FEATURE_CSV, START
from .features import build_panel
from .features.extra import extra_cross_section, extra_single_stock
from .universe import pool_tickers
from .validation import validate_prices, validate_chips, validate_features

F38 = pd.read_csv(FEATURE_CSV).feature.tolist()


def build(stock: dict, verbose: bool = True, *, as_of=None, tickers=None, chips=None) -> pd.DataFrame:
    """stock = {ticker: DataFrame[open high low close volume]} → DataFrame[38 欄]。"""
    if not stock:
        raise ValueError("沒有任何一檔有足夠價格資料")
    if as_of is None:
        raise ValueError("必須明確指定 as_of，不能自動採用快取最新日期")
    day = pd.Timestamp(as_of)
    tickers = pool_tickers() if tickers is None else list(tickers)
    stock = {tk: df.loc[df.index <= day, ["open", "high", "low", "close", "volume"]].copy()
             for tk, df in stock.items()}
    quality = validate_prices(stock, tickers, day)
    end = day.strftime("%Y-%m-%d")
    chips = C.load_chip(tickers, START, end) if chips is None else chips
    quality.update(validate_chips(chips, tickers, day))

    panel, _ = build_panel(stock, lookback_mask=True, verbose=verbose)
    panel = panel.drop(columns=[c for c in panel.columns if c.startswith("p9_")])

    if verbose:
        print(f"[FEAT] 籌碼有檔 {len(chips)}/{len(stock)}", flush=True)
    parts = []
    for tk, df in stock.items():
        x = extra_single_stock(df, chips.get(tk, {}))
        x.index = pd.MultiIndex.from_arrays([df.index, np.repeat(tk, len(df))],
                                            names=["date", "ticker"])
        parts.append(x)
    ex = pd.concat(parts).reindex(panel.index)
    ex = pd.concat([ex, extra_cross_section(pd.concat([panel[["close"]], ex], axis=1))], axis=1)
    ex = ex.replace([np.inf, -np.inf], np.nan).astype("float32")

    full = pd.concat([panel, ex], axis=1)
    miss = [c for c in F38 if c not in full.columns]
    if miss:
        raise ValueError(f"面板缺 {len(miss)} 欄:{miss}")
    X = full[F38]                       # 順序照 feature_shortlist_v2.csv,不可變
    quality.update(validate_features(latest_row(X, day), tickers, F38))
    X.attrs["data_quality"] = quality
    if verbose:
        d = X.index.get_level_values("date")
        print(f"[FEAT] {X.shape}  {d.min().date()} ~ {d.max().date()}", flush=True)
    return X


def latest_row(X: pd.DataFrame, date=None) -> pd.DataFrame:
    """取某一天的橫斷面(index = ticker);date=None 取最後一天。"""
    d = X.index.get_level_values("date")
    day = pd.Timestamp(date) if date is not None else d.max()
    return X.xs(day, level="date")
