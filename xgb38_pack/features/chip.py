"""H. 籌碼族群(FinMind:三大法人買賣超、融資融券、外資持股)。

單位:法人 buy/sell 為股;融資券餘額與限額為張(×1000 股);外資持股比為 %;yfinance volume 為股。
所有量類指標皆除以 20 日均量正規化。

**公布時間**:法人買賣超約收盤後 15–16 時、融資券晚間、外資持股隔日;
因此 build_panel 會把本族群整體 shift(1)(決策日 t 只用 t−1 的籌碼),避免用到收盤時還沒公布的資料。
"""
import numpy as np
import pandas as pd

from ._util import safe_div

INST_COLS = ["inst_foreign_net_r", "inst_trust_net_r", "inst_dealer_net_r", "inst_all_net_r"]


def _streak(x: pd.Series) -> pd.Series:
    """連續同號天數(正為連買、負為連賣)。"""
    s = np.sign(x.fillna(0).values)
    out = np.zeros(len(s))
    for i in range(len(s)):
        if s[i] == 0:
            out[i] = 0
        elif i > 0 and s[i] == s[i - 1]:
            out[i] = out[i - 1] + s[i]
        else:
            out[i] = s[i]
    return pd.Series(out, index=x.index)


def chip_features(price: pd.DataFrame, parts: dict) -> pd.DataFrame:
    idx = price.index
    vol20 = price["volume"].rolling(20).mean()
    f = {}

    inst = parts.get("institutional")
    if inst is not None and len(inst):
        w = inst.pivot_table(index="date", columns="name", values=["buy", "sell"], aggfunc="sum")
        net = (w["buy"] - w["sell"]).reindex(idx)
        has = net.notna().any(axis=1)
        g = lambda c: (net[c].fillna(0.0).where(has) if c in net.columns else pd.Series(0.0, index=idx).where(has))
        foreign = g("Foreign_Investor") + g("Foreign_Dealer_Self")
        trust = g("Investment_Trust")
        dealer = g("Dealer_self") + g("Dealer_Hedging")
        f["inst_foreign_net_r"] = foreign / vol20
        f["inst_trust_net_r"] = trust / vol20
        f["inst_dealer_net_r"] = dealer / vol20
        f["inst_all_net_r"] = (foreign + trust + dealer) / vol20
        for name, s in [("foreign", f["inst_foreign_net_r"]), ("trust", f["inst_trust_net_r"]),
                        ("all", f["inst_all_net_r"])]:
            for w_ in [5, 20, 60]:
                f[f"inst_{name}_sum{w_}"] = s.rolling(w_, min_periods=w_ // 2).sum()
        f["inst_foreign_streak"] = _streak(foreign)
        f["inst_trust_streak"] = _streak(trust)
        f["inst_foreign_pos20"] = (foreign > 0).astype(float).rolling(20).mean()
        f["inst_trust_pos20"] = (trust > 0).astype(float).rolling(20).mean()
        s = f["inst_foreign_net_r"]
        f["inst_foreign_z60"] = safe_div(s - s.rolling(60).mean(), s.rolling(60).std())

    mg = parts.get("margin")
    if mg is not None and len(mg):
        m = mg.drop_duplicates("date").set_index("date").reindex(idx)
        bal = m["MarginPurchaseTodayBalance"].astype(float) * 1000
        sbal = m["ShortSaleTodayBalance"].astype(float) * 1000
        lim = m["MarginPurchaseLimit"].astype(float) * 1000
        slim = m["ShortSaleLimit"].astype(float) * 1000
        for w_ in [1, 5, 20]:
            f[f"mg_bal_chg{w_}"] = bal.diff(w_) / vol20
            f[f"ss_bal_chg{w_}"] = sbal.diff(w_) / vol20
        f["mg_util"] = safe_div(bal, lim)
        f["ss_util"] = safe_div(sbal, slim)
        f["ss_mg_ratio"] = safe_div(sbal, bal)
        f["mg_bal_roc20"] = bal / bal.shift(20) - 1
        f["mg_bal_to_vol"] = bal / vol20

    sh = parts.get("shareholding")
    if sh is not None and len(sh):
        s = sh.drop_duplicates("date").set_index("date").reindex(idx)
        ratio = s["ForeignInvestmentSharesRatio"].astype(float)
        f["sh_foreign_ratio"] = ratio
        for w_ in [5, 20, 60]:
            f[f"sh_foreign_chg{w_}"] = ratio.diff(w_)
        f["sh_foreign_z60"] = safe_div(ratio - ratio.rolling(60).mean(), ratio.rolling(60).std())
        f["sh_foreign_room"] = s["ForeignInvestmentUpperLimitRatio"].astype(float) - ratio

    out = pd.DataFrame(f, index=idx)
    for c in CHIP_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
    return out[CHIP_COLUMNS]


CHIP_COLUMNS = (
    INST_COLS
    + [f"inst_{n}_sum{w}" for n in ("foreign", "trust", "all") for w in (5, 20, 60)]
    + ["inst_foreign_streak", "inst_trust_streak", "inst_foreign_pos20", "inst_trust_pos20",
       "inst_foreign_z60"]
    + [f"{p}_bal_chg{w}" for w in (1, 5, 20) for p in ("mg", "ss")]
    + ["mg_util", "ss_util", "ss_mg_ratio", "mg_bal_roc20", "mg_bal_to_vol"]
    + ["sh_foreign_ratio", "sh_foreign_chg5", "sh_foreign_chg20", "sh_foreign_chg60",
       "sh_foreign_z60", "sh_foreign_room"]
)

CHIP_RANK_COLS = ["inst_foreign_sum20", "inst_trust_sum20", "inst_all_sum5",
                  "mg_bal_chg20", "ss_bal_chg5", "sh_foreign_chg20"]
