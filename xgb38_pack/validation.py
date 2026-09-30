"""Fail closed on absent/stale inputs, while retaining documented model NaNs."""
import re

import numpy as np
import pandas as pd

from .chip_data import DATASETS, REQUIRED_COLUMNS
from .config import WARMUP_ROWS

PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


def parse_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        raise ValueError("日期格式必須是 YYYY-MM-DD")
    day = pd.Timestamp(value)
    if pd.isna(day):
        raise ValueError("日期不得為空")
    return day


def validate_prices(stock, tickers, as_of, warmup=WARMUP_ROWS):
    day = pd.Timestamp(as_of)
    missing = sorted(set(tickers) - set(stock))
    extra = sorted(set(stock) - set(tickers))
    if missing or extra:
        raise ValueError(f"股票池不完整: 缺 {missing}，多 {extra}")
    errors = []
    for ticker in tickers:
        frame = stock[ticker]
        if frame.empty:
            errors.append(f"{ticker}: 指定日期之前沒有價格")
            continue
        if set(PRICE_COLUMNS) - set(frame.columns):
            errors.append(f"{ticker}: 缺 OHLCV 欄位")
            continue
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            errors.append(f"{ticker}: 日期重複或未排序")
        if frame.index.max() != day:
            errors.append(f"{ticker}: 最後資料 {frame.index.max().date()}，要求 {day.date()}")
        if len(frame) < warmup:
            errors.append(f"{ticker}: 僅 {len(frame)} 筆，需要 {warmup} 筆暖機資料")
        recent = frame[PRICE_COLUMNS].tail(warmup).to_numpy(dtype=float)
        if not np.isfinite(recent).all() or (recent[:, :4] <= 0).any() or (recent[:, 4] < 0).any():
            errors.append(f"{ticker}: 暖機區間 OHLCV 含缺值或無效數值")
    if errors:
        raise ValueError("價格資料未通過檢查；請指定完整交易日，不會自動退回舊日期:\n" + "\n".join(errors))
    return {"price_tickers": len(stock), "price_as_of": str(day.date()),
            "minimum_price_rows": min(len(frame) for frame in stock.values()),
            "required_warmup_rows": warmup}


def validate_chips(chips, tickers, as_of):
    day = pd.Timestamp(as_of)
    errors = []
    for ticker in tickers:
        for key in DATASETS:
            frame = chips.get(ticker, {}).get(key)
            if frame is None or frame.empty:
                errors.append(f"{ticker}/{key}: 整個來源缺失")
                continue
            missing = set(REQUIRED_COLUMNS[key]) - set(frame.columns)
            if missing:
                errors.append(f"{ticker}/{key}: 缺少欄位 {sorted(missing)}")
                continue
            dates = pd.to_datetime(frame.date)
            if dates.max() != day:
                errors.append(f"{ticker}/{key}: 最後資料 {dates.max().date()}，要求 {day.date()}")
                continue
            latest = frame.loc[dates == day]
            numeric = [c for c in REQUIRED_COLUMNS[key] if c not in {"date", "stock_id", "name"}]
            values = latest[numeric].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(values).all():
                errors.append(f"{ticker}/{key}: 當日必要數值缺失")
            if key == "institutional" and "Investment_Trust" not in set(latest.name):
                errors.append(f"{ticker}/{key}: 當日缺投信資料")
    if errors:
        raise ValueError("籌碼資料未通過檢查:\n" + "\n".join(errors))
    return {"chip_tickers": len(tickers), "chip_datasets": list(DATASETS),
            "chip_as_of": str(day.date()), "chip_shift": 0,
            "availability_note": "日期完整不等於歷史發布時間已驗證；維持原模型 chip_shift=0"}


def validate_features(frame, tickers, columns):
    if list(frame.columns) != list(columns):
        raise ValueError("特徵欄位或順序不符")
    if frame.index.has_duplicates or set(frame.index) != set(tickers):
        raise ValueError("推論橫斷面不等於完整股票池")
    values = frame.to_numpy(dtype=np.float32)
    if np.isinf(values).any():
        raise ValueError("特徵含 infinity")
    absent = frame.columns[frame.isna().all()].tolist()
    if absent:
        raise ValueError(f"整個股票池的必要特徵皆缺值: {absent}")
    if frame.isna().all(axis=1).any():
        raise ValueError("有股票的全部特徵缺失")
    # With fresh, finite sources and enough price history already validated,
    # individual NaNs can still arise from zero range/variance/limits, or short
    # chip histories. Preserve the original feature formulas and model semantics.
    missing = {ticker: frame.columns[row.isna()].tolist()
               for ticker, row in frame.iterrows() if row.isna().any()}
    return {"feature_count": len(columns), "feature_tickers": len(frame),
            "nan_features_by_ticker": missing, "source_checks_passed": True,
            "nan_policy": "資料來源與暖機先檢查；保留公式產生的個別 NaN，不填零；整欄或整列缺失拒絕推論"}
