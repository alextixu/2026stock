"""2026-09-21 新增的三群特徵:長週期動能、隔夜 / 日內拆分、籌碼。全部只用第 t 日收盤後已知的資料。

  長週期動能(單股): mom_250_21(12−1 個月動能,Jegadeesh & Titman)、mom_250_126(12−7 個月,Novy-Marx 2012)、mom_126_21、
      dist_high_250(52 週高點距離,George & Hwang 2004)、mad_21_200(21 日均線 ÷ 200 日均線 − 1,Avramov 等 2021)、sma_ratio_250、
      fip_250 / fip_126(資訊連續性,Da、Gurun、Warachka 2014:累積報酬方向 ×(下跌天數比例 − 上漲天數比例),越負 = 越是小步慢漲)、
      mom_voladj_250_21(12−1 動能 ÷ 同期波動)。
  隔夜 / 日內(單股,Lou、Polk、Skouras 2019): on_ret_w = 過去 w 日 ln(開盤 ÷ 前收) 的總和;id_ret_w = 過去 w 日 ln(收盤 ÷ 開盤) 的總和;
      on_id_diff_w = 兩者之差。還原價的開盤與收盤用同一個還原係數,除息日不會有假跳空。
  橫斷面(要整個面板): resid_mom_250_21(殘差動能,Blitz 等 2011:日報酬 − 前一日估的 250 日 beta × 池內等權報酬,在 t−250 ~ t−21 累加後除以殘差標準差)、
      persist_12(過去 12 個 21 日區間裡,報酬高於池內等權的比例;台灣動能靠持續性,Revisiting the momentum effect in Taiwan 2023)。
  籌碼: features/chip.py 的 35 欄 + 6 欄橫斷面排名。**2026-09-21 起對齊當日(第 T 日)**:標籤是 T+1 開盤才進場,第 T 日盤後公布的
      三大法人買賣超、融資融券餘額在進場前已經拿得到(chip_shift=0)。注意:外資持股比例(sh_foreign_*)的公布時間較晚,
      是否趕得上 T+1 開盤尚未逐項查證;要保守可設 chip_shift=1 回到延後一日。
      籌碼資料比股價晚開始的股票(例如上市滿半年才能信用交易),回看期從「籌碼第一筆有值」起算。
"""
import re
import numpy as np
import pandas as pd
from .chip import chip_features, CHIP_COLUMNS, CHIP_RANK_COLS

ON_W = (5, 20, 60, 120, 250)


def longmom_features(df: pd.DataFrame) -> dict:
    c, h = df["close"], df["high"]
    r = c.pct_change(); f = {}
    f["mom_250_21"] = c.shift(21) / c.shift(250) - 1
    f["mom_250_126"] = c.shift(126) / c.shift(250) - 1
    f["mom_126_21"] = c.shift(21) / c.shift(126) - 1
    f["dist_high_250"] = c / h.rolling(250).max() - 1
    f["mad_21_200"] = c.rolling(21).mean() / c.rolling(200).mean() - 1
    f["sma_ratio_250"] = c / c.rolling(250).mean() - 1
    for w in (250, 126):
        n = w - 21; rs = r.shift(21)
        pos, neg = (rs > 0).astype(float).rolling(n).mean(), (rs < 0).astype(float).rolling(n).mean()
        f[f"fip_{w}"] = np.sign(c.shift(21) / c.shift(w) - 1) * (neg - pos)
    f["mom_voladj_250_21"] = f["mom_250_21"] / (r.shift(21).rolling(229).std() * np.sqrt(229))
    return f


def overnight_features(df: pd.DataFrame) -> dict:
    o, c = df["open"], df["close"]
    on, idr = np.log(o / c.shift(1)), np.log(c / o); f = {}
    for w in ON_W:
        f[f"on_ret_{w}"] = on.rolling(w).sum(); f[f"id_ret_{w}"] = idr.rolling(w).sum()
    for w in (20, 60, 250):
        f[f"on_id_diff_{w}"] = f[f"on_ret_{w}"] - f[f"id_ret_{w}"]
    return f


def extra_single_stock(df: pd.DataFrame, chip_parts: dict, chip_shift: int = 0) -> pd.DataFrame:
    f = {**longmom_features(df), **overnight_features(df)}
    out = pd.DataFrame(f, index=df.index)
    for col in out.columns:
        k = max(int(x) for x in re.findall(r"\d+", col))
        out.iloc[:min(k, len(out)), out.columns.get_loc(col)] = np.nan
    ch = chip_features(df, chip_parts)
    for col in ch.columns:
        nums = [int(x) for x in re.findall(r"\d+", col)]; k = (max(nums) if nums else (20 if col == "mg_bal_to_vol" else 1))
        v = ch[col].values; first = np.flatnonzero(~np.isnan(v))
        if len(first):
            ch.iloc[:min(first[0] + k, len(ch)), ch.columns.get_loc(col)] = np.nan
    return pd.concat([out, ch.shift(chip_shift) if chip_shift else ch], axis=1)


def extra_cross_section(panel: pd.DataFrame) -> pd.DataFrame:
    """panel 需含 close 與籌碼欄;回傳 resid_mom_250_21、persist_12、cs_rank_<籌碼>。"""
    out = pd.DataFrame(index=panel.index)
    cw = panel["close"].unstack("ticker"); rw = cw.pct_change(fill_method=None); mkt = rw.mean(axis=1)
    beta = rw.rolling(250, min_periods=250).cov(mkt).div(mkt.rolling(250, min_periods=250).var(), axis=0).shift(1)
    resid = rw.sub(beta.mul(mkt, axis=0))
    rs = resid.shift(21); num, sd = rs.rolling(229, min_periods=229).sum(), rs.rolling(229, min_periods=229).std()
    out["resid_mom_250_21"] = (num / (sd * np.sqrt(229))).stack(future_stack=True).reindex(panel.index)
    mk_cum = (1 + mkt.fillna(0)).cumprod(); wins = []
    for k in range(12):
        sr = cw.shift(21 * k) / cw.shift(21 * (k + 1)) - 1; mr = mk_cum.shift(21 * k) / mk_cum.shift(21 * (k + 1)) - 1
        wins.append(sr.gt(mr, axis=0).astype(float).where(sr.notna()))
    out["persist_12"] = (sum(wins) / 12).stack(future_stack=True).reindex(panel.index)
    g = panel.groupby(level="date")
    for col in CHIP_RANK_COLS:
        out[f"cs_rank_{col}"] = g[col].rank(pct=True)
    return out
