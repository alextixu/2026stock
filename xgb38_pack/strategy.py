"""交易規則層:排名 → BUY / SELL / HOLD + 張數 + 委託價。

目前整合入口僅使用此模組產生預覽。以下為原策略假設，尚非可執行的成交流程。

移植自回測用的 run_logic2.Logic2(2026-09-26 定案的規則),語意逐條對照:

  第 T 日收盤後 ── 用 T 日特徵算分數,排出全池名次。
    1. 汰弱留強:持股名次 ≤ EXIT_RANK 就續抱(out_cnt 歸零);掉出則 out_cnt+1,
       連續 OUT_DAYS 天掉出才賣。續抱者保留原進場價與停損停利價。
    2. 買進候選:名次前 MAXRANK 名,且 5 日均量 ≥ MIN_LOTS 張。
    3. 空出幾格就補幾檔(TOPK − 續抱數),照名次順序補。
    4. 每格資金 = 總資產 / TOPK。
  第 T+1 日開盤 ── 上面的賣單與買單全部以開盤價送出。
  停損停利 ── 進場時就定好:停損 = 進場價 − ATR_MULT × ATR14(T 日為止),停利 = 進場價 × 1.3。

與回測的兩處實務差異(README 有詳述):
  a) 回測是用 T+1 當天的 low / high 判斷停損停利、以 min(open, sl) / max(open, tp) 成交。
     實單沒有盤中回呼,本模組改成「用 T 日已收的 high / low 判斷是否已觸價,觸了就在 T+1 開盤市價出場」,
     並同時輸出每檔的 sl / tp 價位,方便你自己掛條件單。這會讓出場比回測晚一天。
  b) 回測允許零股;Buy_Stock 的 stock_shares 是張。SHARES_UNIT="lot" 時買不起整張就跳過該檔。
"""
import json
import os

import numpy as np
import pandas as pd

from .config import (ATR_MULT, ATR_WIN, EXIT_RANK, LOT_SIZE, MAXRANK, MIN_LOTS,
                    OUT_DAYS, SHARES_UNIT, STATE_FP, TAKE_PCT, TOPK)


# ───────────────────────── 量價指標 ─────────────────────────
def atr14(df: pd.DataFrame) -> pd.Series:
    """ATR14,與回測 run_logic.load_indicators 同一條公式(未還原價、min_periods=7)。"""
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(ATR_WIN, min_periods=ATR_WIN // 2).mean()


def vol5_lots(df: pd.DataFrame) -> pd.Series:
    """5 日均量(張)。volume 是股數,除以 1000。"""
    return (df["volume"].astype(float) / 1000).rolling(5).mean()


# ───────────────────────── 持倉狀態 ─────────────────────────
def load_state(fp=STATE_FP) -> dict:
    """{code: {entry, sl, tp, shares, entry_date, out_cnt}};檔案不存在視為空手。"""
    if not os.path.exists(fp):
        return {}
    with open(fp, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, fp=STATE_FP):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def sync_state(state: dict, broker_holdings: dict) -> dict:
    """用 Get_User_Stocks 的實際持股對帳:券商沒有的就從狀態移除,券商有但狀態沒有的補一筆(無停損停利)。

    broker_holdings: {code: shares}。回傳對帳後的 state,並把差異印出來。
    """
    out = dict(state)
    for c in list(out):
        if c not in broker_holdings:
            print(f"  [對帳] {c} 在本地狀態但券商沒有 → 移除")
            out.pop(c)
    for c, sh in broker_holdings.items():
        if c not in out:
            print(f"  [對帳] {c} 券商有 {sh} 但本地狀態沒有 → 補入(無停損停利價,建議手動補)")
            out[c] = {"entry": None, "sl": None, "tp": None, "shares": sh,
                      "entry_date": None, "out_cnt": 0}
        elif out[c].get("shares") != sh:
            print(f"  [對帳] {c} 張數不符 本地 {out[c].get('shares')} → 券商 {sh}")
            out[c]["shares"] = sh
    return out


# ───────────────────────── 計畫 ─────────────────────────
def _round_shares(cash_per_slot, price):
    """回傳 (下單數量, 單位字串, 實際金額)。"lot" 買不起一張就回 0。"""
    if price is None or not np.isfinite(price) or price <= 0:
        return 0, "張" if SHARES_UNIT == "lot" else "股", 0.0
    if SHARES_UNIT == "lot":
        n = int(cash_per_slot // (price * LOT_SIZE))
        return n, "張", n * price * LOT_SIZE
    n = int(cash_per_slot // price)
    return n, "股", n * price


def make_plan(rank: pd.DataFrame, px_data: dict, state: dict, cash: float,
              as_of: pd.Timestamp, *, conservative=False, budget_buffer=0.0) -> dict:
    """rank: DataFrame[score, rank],index 是 ticker(如 2330.TW)。
    px_data: {ticker: 價格 DataFrame},用來算 ATR、5 日均量與參考價。
             ref_price 只用於估張數與停損停利價位,實際成交價由交易系統決定。
    state: 目前持倉狀態。cash: 可用現金。
    回傳交易計畫 dict(不下單)。"""
    code = lambda tk: tk.split(".")[0]
    rank = rank.copy()
    rank["code"] = [code(t) for t in rank.index]
    rk = dict(zip(rank["code"], rank["rank"]))

    # 目前持股市值(用 T 日收盤的未還原價)
    def last_close(c):
        for tk, df in px_data.items():
            if code(tk) == c and len(df):
                return round(float(df["close"].iloc[-1]), 2)
        return None

    holdings_value = 0.0
    for c, p in state.items():
        px = last_close(c)
        sh = (p.get("shares") or 0) * (LOT_SIZE if SHARES_UNIT == "lot" else 1)
        if px:
            holdings_value += sh * px
    equity = cash + holdings_value

    # ── 1) 汰弱留強 ────────────────────────────────────────
    keep, drop = {}, {}
    for c, p in state.items():
        r = rk.get(c, 999)
        cnt = 0 if r <= EXIT_RANK else p.get("out_cnt", 0) + 1
        p = dict(p, out_cnt=cnt)
        (keep if cnt < OUT_DAYS else drop)[c] = dict(p, rank=r)

    # ── 2) 停損停利(用 T 日已收的 high/low 判斷是否已觸價) ──
    trig = {}
    for c, p in list(keep.items()):
        df = next((d for tk, d in px_data.items() if code(tk) == c), None)
        if df is None or not len(df) or p.get("entry") is None:
            continue
        hi, lo = float(df["high"].iloc[-1]), float(df["low"].iloc[-1])
        if p.get("sl") is not None and lo <= p["sl"]:
            trig[c] = dict(keep.pop(c), reason="停損", level=p["sl"])
        elif p.get("tp") is not None and hi >= p["tp"]:
            trig[c] = dict(keep.pop(c), reason="停利", level=p["tp"])

    # ── 3) 買進候選:前 MAXRANK 名 + 流動性 ─────────────────
    cands, liq_out = [], []
    for tk, row in rank.head(MAXRANK).iterrows():
        c = row["code"]
        if c in keep or (conservative and (c in drop or c in trig)):
            continue
        df = next((d for t, d in px_data.items() if code(t) == c), None)
        if df is None or len(df) < 5:
            liq_out.append((c, "無足夠價格資料"))
            continue
        v5 = float(vol5_lots(df).iloc[-1])
        if not np.isfinite(v5) or v5 < MIN_LOTS:
            liq_out.append((c, f"5日均量 {v5:,.0f} 張 < {MIN_LOTS}"))
            continue
        cands.append((c, int(row["rank"]), float(row["score"]), df))

    # ── 4) 補格 ────────────────────────────────────────────
    slots = TOPK - len(keep)
    per_slot = equity / TOPK if TOPK else 0.0
    buys, skipped = [], []
    avail = cash + sum(
        (p.get("shares") or 0) * (LOT_SIZE if SHARES_UNIT == "lot" else 1) * (last_close(c) or 0)
        for c, p in list(drop.items()) + list(trig.items()))       # 賣掉的錢可以再投入
    if conservative:
        avail = cash  # A reservation does not make sale proceeds available.
    for c, r, sc, df in cands:
        if slots <= 0:
            break
        px = round(float(df["close"].iloc[-1]), 2)       # 參考價 = T 日收盤
        n, unit, amt = _round_shares(min(avail, per_slot) / (1 + budget_buffer), px)
        if n <= 0:
            skipped.append((c, r, px, f"每格 {min(avail, per_slot):,.0f} 元買不起 1 {unit}"))
            continue
        a = float(atr14(df).iloc[-1])
        buys.append({"code": c, "rank": r, "score": round(sc, 5), "ref_price": px,
                     "shares": n, "unit": unit, "amount": round(amt, 0),
                     "atr14": round(a, 2) if np.isfinite(a) else None,
                     "stop_loss": round(px - ATR_MULT * a, 2) if np.isfinite(a) else None,
                     "take_profit": round(px * (1 + TAKE_PCT), 2)})
        avail -= amt * (1 + budget_buffer)
        slots -= 1

    return {
        "as_of": str(pd.Timestamp(as_of).date()),
        "equity": round(equity, 0), "cash": round(cash, 0),
        "sells": [{"code": c, "reason": "排名掉出", "rank": p["rank"],
                   "shares": p.get("shares"), "ref_price": last_close(c)} for c, p in drop.items()]
                 + [{"code": c, "reason": p["reason"], "level": p["level"],
                     "shares": p.get("shares"), "ref_price": last_close(c)} for c, p in trig.items()],
        "holds": [{"code": c, "rank": p["rank"], "shares": p.get("shares"),
                   "stop_loss": p.get("sl"), "take_profit": p.get("tp"),
                   "out_cnt": p["out_cnt"]} for c, p in keep.items()],
        "buys": buys,
        "skipped_unaffordable": [{"code": c, "rank": r, "ref_price": px, "why": w}
                                 for c, r, px, w in skipped],
        "filtered_by_liquidity": [{"code": c, "why": w} for c, w in liq_out],
        "next_state": {**{c: {k: v for k, v in p.items() if k != "rank"} for c, p in keep.items()},
                       **{b["code"]: {"entry": b["ref_price"], "sl": b["stop_loss"],
                                      "tp": b["take_profit"], "shares": b["shares"],
                                      "entry_date": str(pd.Timestamp(as_of).date()), "out_cnt": 0}
                          for b in buys}},
    }
