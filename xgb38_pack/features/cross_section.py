"""G. 橫斷面族群:需要整個股票池的 panel,只用當日與過去資訊。

panel: DataFrame, MultiIndex (date, ticker), 已含單股特徵。
"""
import numpy as np
import pandas as pd

from ._util import W

RANK_COLS = ["roc_5", "roc_20", "roc_60", "roc_120", "ret_std_20", "ret_std_60",
             "vol_z_20", "turnover_z_20", "rsi_14"]


def cross_section_features(panel: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=panel.index)
    g = panel.groupby(level="date")
    for col in RANK_COLS:
        out[f"cs_rank_{col}"] = g[col].rank(pct=True)

    r_wide = panel["ret_lag_1"].unstack("ticker")
    mkt_r = r_wide.mean(axis=1)
    mkt_cum = (1 + mkt_r.fillna(0)).cumprod()
    mkt_roc = {w: mkt_cum / mkt_cum.shift(w) - 1 for w in W}
    for w in W:
        roc = panel[f"roc_{w}"].unstack("ticker")
        out[f"cs_excess_roc_{w}"] = (roc.sub(mkt_roc[w], axis=0)).stack(future_stack=True).reindex(panel.index)
    for w in [5, 20, 60]:
        out[f"mkt_roc_{w}"] = mkt_roc[w].reindex(panel.index.get_level_values("date")).values

    cov = r_wide.rolling(60).cov(mkt_r)
    var = mkt_r.rolling(60).var()
    beta = cov.div(var, axis=0)
    out["cs_beta_60"] = beta.stack(future_stack=True).reindex(panel.index)
    resid = r_wide.sub(mkt_r, axis=0)
    out["cs_idio_std_60"] = resid.rolling(60).std().stack(future_stack=True).reindex(panel.index)
    out["cs_rank_excess_roc_20"] = out.groupby(level="date")["cs_excess_roc_20"].rank(pct=True)
    from .chip import CHIP_RANK_COLS
    for col in CHIP_RANK_COLS:
        if col in panel.columns:
            out[f"cs_rank_{col}"] = g[col].rank(pct=True)
    return out
