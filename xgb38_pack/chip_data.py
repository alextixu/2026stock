"""FinMind incremental downloads. Credentials are read from environment variables.

Run from the project root: python -m xgb38_pack.chip_data --end YYYY-MM-DD
FINMIND_TOKEN: optional bearer token. FINMIND_API_URL: optional endpoint override.
"""
import argparse
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import pandas as pd
import requests

from .config import CHIP_DIR, START
from .universe import pool_tickers

DATA_DIR = os.environ.get("FINMIND_DIR") or CHIP_DIR
DEFAULT_API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASETS = {
    "institutional": "TaiwanStockInstitutionalInvestorsBuySell",
    "margin": "TaiwanStockMarginPurchaseShortSale",
    "shareholding": "TaiwanStockShareholding",
}
REQUIRED_COLUMNS = {
    "institutional": ["date", "stock_id", "name", "buy", "sell"],
    "margin": ["date", "stock_id", "MarginPurchaseTodayBalance", "ShortSaleTodayBalance",
               "MarginPurchaseLimit", "ShortSaleLimit"],
    "shareholding": ["date", "stock_id", "ForeignInvestmentSharesRatio",
                    "ForeignInvestmentUpperLimitRatio"],
}


def api_settings():
    """Read at request time; never log the token."""
    url = os.environ.get("FINMIND_API_URL", DEFAULT_API_URL).strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("FINMIND_API_URL 必須是 HTTPS API 網址，不可夾帶帳密、query 或 fragment")
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    return url, {"Authorization": f"Bearer {token}"} if token else {}


def stock_id(ticker):
    return ticker.split(".")[0]


def _path(key, sid):
    return os.path.join(DATA_DIR, key, f"{sid}.parquet")


def normalize_chip(df, key, sid):
    """Institutional rows are unique by date AND institution, not date alone."""
    missing = set(REQUIRED_COLUMNS[key]) - set(df.columns)
    if missing:
        raise ValueError(f"{sid}/{key} 缺少欄位: {sorted(missing)}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="raise").dt.normalize()
    if df["date"].isna().any() or not df["stock_id"].astype(str).eq(str(sid)).all():
        raise ValueError(f"{sid}/{key} 日期或股票代號不符")
    keys = ["date", "stock_id"] + (["name"] if key == "institutional" else [])
    if df[keys].isna().any().any():
        raise ValueError(f"{sid}/{key} 資料鍵包含缺值")
    return df.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)


def _write_cache(df, fp):
    path = Path(fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    df.to_parquet(temporary, index=False)
    temporary.replace(path)


def fetch_one(key, sid, start, end, sleep=1.0, max_retry=3):
    if key not in DATASETS:
        raise ValueError(f"未知籌碼類別: {key}")
    begin, cutoff = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if begin > cutoff:
        raise ValueError("籌碼起日不可晚於迄日")
    fp = _path(key, sid)
    old = normalize_chip(pd.read_parquet(fp), key, sid) if os.path.exists(fp) else None
    request_start = begin
    if old is not None and len(old):
        if old.date.max() >= cutoff:
            return "cached"
        request_start = max(begin, old.date.max() - pd.Timedelta(days=10))
    url, headers = api_settings()
    params = {"dataset": DATASETS[key], "data_id": str(sid),
              "start_date": request_start.strftime("%Y-%m-%d"),
              "end_date": cutoff.strftime("%Y-%m-%d")}
    for attempt in range(max_retry):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code in (402, 429):
                raise RuntimeError(f"FinMind 額度限制 HTTP {response.status_code}；停止更新，稍後重試")
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") in (402, 429):
                raise RuntimeError("FinMind 回報額度限制；停止更新，稍後重試")
            if payload.get("status") != 200:
                raise RuntimeError(f"FinMind 回報非成功狀態: {payload.get('status')}")
            new = pd.DataFrame(payload.get("data", []))
            if new.empty:
                return "empty"
            new = normalize_chip(new, key, sid)
            new = new.loc[new.date.between(request_start, cutoff)]
            if new.empty:
                return "empty"
            merged = new if old is None else pd.concat([old, new], ignore_index=True)
            _write_cache(normalize_chip(merged, key, sid), fp)
            if sleep:
                time.sleep(sleep)
            return "ok" if new.date.max() == cutoff else "stale"
        except requests.RequestException as exc:
            # Do not print response bodies, headers, URLs or credential values.
            if attempt + 1 == max_retry:
                raise RuntimeError(f"{sid}/{key} FinMind 連線失敗 ({type(exc).__name__})") from None
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{sid}/{key} 未完成下載")


def download_pool(tickers=None, start=START, end=None, keys=None):
    tickers = pool_tickers() if tickers is None else list(tickers)
    if end is None:
        raise ValueError("請指定籌碼資料截止日 end")
    keys = list(DATASETS) if keys is None else list(keys)
    if not keys or set(keys) - set(DATASETS):
        raise ValueError("keys 必須是 institutional、margin、shareholding")
    stats = {}
    total = len(tickers) * len(keys)
    done = 0
    for key in keys:
        for ticker in tickers:
            status = fetch_one(key, stock_id(ticker), start, end)
            stats[status] = stats.get(status, 0) + 1
            done += 1
            if done % 10 == 0:
                print(f"[CHIP] {done}/{total} {stats}", flush=True)
    print(f"[CHIP] 完成 {stats}")
    return stats


def load_chip(tickers, start, end):
    """Read only, clip dates; callers validate missing/stale data before inference."""
    out = {}
    for ticker in tickers:
        sid = stock_id(ticker)
        parts = {}
        for key in DATASETS:
            fp = _path(key, sid)
            if not os.path.exists(fp):
                continue
            df = normalize_chip(pd.read_parquet(fp), key, sid)
            df = df.loc[df.date.between(pd.Timestamp(start), pd.Timestamp(end))].copy()
            if len(df):
                parts[key] = df
        if parts:
            out[ticker] = parts
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", required=True)
    parser.add_argument("--keys", default=None, help="逗號分隔的籌碼類別")
    args = parser.parse_args()
    stats = download_pool(start=args.start, end=args.end,
                          keys=args.keys.split(",") if args.keys else None)
    if stats.get("empty") or stats.get("stale"):
        parser.exit(1, "籌碼尚未全部更新到指定日；不可視為完整資料。\n")


if __name__ == "__main__":
    main()
