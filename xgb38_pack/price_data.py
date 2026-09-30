"""Price inputs for model scoring and execution planning.

Model features always use yfinance adjusted OHLCV (``auto_adjust=True``)
because the packaged models in ``models/adjpx`` were trained on that basis.
Actual order planning separately calls :func:`fetch_execution_price`, which
reads unadjusted exchange prices through the repository's public ``stock_api``.
"""
import os
import time

import pandas as pd

from .config import CACHE_DIR, START

COLS = ["open", "high", "low", "close", "volume"]


def _path(dirpath, ticker):
    return os.path.join(dirpath, f"{ticker.replace('.', '_')}.parquet")


def fetch_execution_price(code, start, end):
    """Return unadjusted OHLCV for one order-planning stock via stock_api."""
    from stock_api import get_taiwan_stock_data

    df = get_taiwan_stock_data(code, start, end)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLS)
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    out = pd.DataFrame({
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["capacity"], errors="coerce"),
    })
    out.index = df["date"]
    return out


def _fetch_adjpx(ticker, start, end):
    import yfinance as yf

    # yfinance end is exclusive; this package's end is inclusive.
    exclusive_end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(ticker, start=start, end=exclusive_end, auto_adjust=True,
                     progress=False, threads=False)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[COLS]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def _normalize(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], name="date"))
    missing = set(COLS) - set(df.columns)
    if missing:
        raise ValueError(f"價格缺少欄位: {sorted(missing)}")
    df = df[COLS].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    if df.index.isna().any():
        raise ValueError("價格日期包含 NaT")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for col in COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.astype(float)


def _load(tickers, end, dirpath, refresh, verbose, label):
    cutoff = pd.Timestamp(end).normalize()
    if refresh:
        os.makedirs(dirpath, exist_ok=True)
    out, t0 = {}, time.time()
    for i, ticker in enumerate(tickers, 1):
        fp = _path(dirpath, ticker)
        old = _normalize(pd.read_parquet(fp)) if os.path.exists(fp) else None
        if not refresh:
            if old is not None:
                out[ticker] = old.loc[old.index <= cutoff].copy()
            continue
        start = START
        if old is not None and len(old):
            if old.index.max() >= cutoff:
                out[ticker] = old.loc[old.index <= cutoff].copy()
                continue
            # Corporate actions can change the adjusted history. Refresh the
            # complete cached range instead of joining two adjustment bases.
            start = old.index.min().strftime("%Y-%m-%d")
        try:
            new = _fetch_adjpx(ticker, start, end)
        except Exception as exc:
            print(f"  [WARN] {ticker} 抓取失敗 {type(exc).__name__}: {exc}")
            new = pd.DataFrame(columns=COLS)
        new = _normalize(new)
        new = new.loc[new.index <= cutoff]
        if not new.empty:
            if old is not None and len(old) and new.index.min() > old.index.min():
                raise ValueError(f"{ticker} 還原價更新未涵蓋原快取起點，拒絕混接不同還原基準")
            new.to_parquet(fp)
            out[ticker] = new
        elif old is not None:
            out[ticker] = old.loc[old.index <= cutoff].copy()
        if verbose and i % 10 == 0:
            print(f"  {label} {i}/{len(tickers)}", flush=True)
    if verbose:
        last = max((frame.index.max() for frame in out.values()), default=None)
        print(f"[{label}] adjpx {len(out)}/{len(tickers)} 檔,"
              f"最後一根 {last.date() if last is not None else '—'}  {time.time()-t0:.0f}s", flush=True)
    return out


def load_pool(tickers, end, refresh=True, verbose=True):
    """Load adjusted model prices through the inclusive ``end`` date."""
    directory = os.path.join(CACHE_DIR, "price", "adjpx")
    return _load(tickers, end, directory, refresh, verbose, "PRICE")
