"""Train one quarterly adjpx model ensemble from the packaged feature pipeline."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from xgb38_pack import chip_data as C
from xgb38_pack import features_build as F
from xgb38_pack import price_data as PX
from xgb38_pack.config import CACHE_DIR, HERE, SEEDS, START
from xgb38_pack.universe import pool_tickers


def quarter_start(value):
    if len(value) != 6 or value[4] != "Q" or value[5] not in "1234":
        raise ValueError("季度格式必須是 YYYYQ1～YYYYQ4")
    year, quarter = int(value[:4]), int(value[5])
    return pd.Timestamp(year=year, month=1 + (quarter - 1) * 3, day=1)


def load_training_panel(cutoff):
    tickers = pool_tickers()
    end = pd.Timestamp(cutoff).strftime("%Y-%m-%d")
    stock = PX.load_pool(tickers, end, refresh=False)
    chips = C.load_chip(tickers, START, end)
    features = F.build(stock, as_of=end, tickers=tickers, chips=chips)
    return tickers, stock, features


def append_label_day(stock, tickers, label_day, online):
    """Append one later day needed only for the final T+1 label."""
    day = pd.Timestamp(label_day).strftime("%Y-%m-%d")
    out = {}
    for ticker in tickers:
        frame = stock[ticker].copy()
        if frame.index.max() < pd.Timestamp(day):
            if not online:
                raise ValueError(f"{ticker} 缺少標籤日 {day}；請加 --online-label")
            extra = PX._normalize(PX._fetch_adjpx(ticker, day, day))
            frame = PX._normalize(pd.concat([frame, extra]))
        out[ticker] = frame.loc[frame.index <= pd.Timestamp(day)]
    return out


def make_target(stock):
    parts = []
    for ticker, frame in stock.items():
        value = frame["close"].shift(-1).div(frame["open"].shift(-1)).sub(1)
        value.index = pd.MultiIndex.from_arrays(
            [frame.index, np.repeat(ticker, len(frame))], names=["date", "ticker"]
        )
        parts.append(value)
    raw = pd.concat(parts).sort_index()
    return raw.groupby(level="date").rank(method="average", pct=True).rename("target")


def trading_calendar(stock):
    """Return sessions shared by every stock in the model universe."""
    calendars = [set(frame.index.normalize()) for frame in stock.values()]
    return pd.DatetimeIndex(sorted(set.intersection(*calendars)))


def split_data(features, target, start, calendar):
    dates = features.index.get_level_values("date")
    train_start = start - pd.DateOffset(years=5)
    valid_start = start - pd.DateOffset(years=1)
    train_sessions = calendar[(calendar >= train_start) & (calendar < valid_start)]
    valid_sessions = calendar[(calendar >= valid_start) & (calendar < start)]
    if not len(train_sessions) or not len(valid_sessions):
        raise ValueError("訓練窗口沒有特徵")
    last_train_day = train_sessions.max()
    last_valid_day = valid_sessions.max()
    train_mask = (dates >= train_start) & (dates < valid_start) & (dates != last_train_day)
    valid_mask = (dates >= valid_start) & (dates < start) & (dates != last_valid_day)
    joined = features.join(target)
    train = joined.loc[train_mask].dropna(subset=["target"])
    valid = joined.loc[valid_mask].dropna(subset=["target"])
    if train.empty or valid.empty:
        raise ValueError("訓練或驗證資料為空")
    return train, valid, last_train_day, last_valid_day


def daily_metrics(index, target, prediction):
    frame = pd.DataFrame({"target": target, "prediction": prediction}, index=index)
    rank_ic = frame.groupby(level="date").apply(
        lambda day: day["target"].corr(day["prediction"], method="spearman"),
        include_groups=False,
    ).mean()
    top3 = frame.assign(rank=frame.groupby(level="date")["prediction"].rank(
        method="first", ascending=False
    ))
    return float(rank_ic), float(top3.loc[top3["rank"] <= 3, "target"].mean())


def train(args):
    start = quarter_start(args.quarter)
    feature_cutoff = pd.Timestamp(args.feature_cutoff or (start - pd.Timedelta(days=1)))
    label_day = pd.Timestamp(args.label_day or (feature_cutoff + pd.Timedelta(days=1)))
    tickers, stock, features = load_training_panel(feature_cutoff)
    stock = append_label_day(stock, tickers, label_day, args.online_label)
    target = make_target(stock)
    train_frame, valid_frame, removed_train_day, removed_valid_day = split_data(
        features, target, start, trading_calendar(stock)
    )
    columns = F.F38
    X_train = train_frame[columns].to_numpy(dtype=np.float32)
    y_train = train_frame["target"].to_numpy(dtype=np.float32)
    X_valid = valid_frame[columns].to_numpy(dtype=np.float32)
    y_valid = valid_frame["target"].to_numpy(dtype=np.float32)
    destination = Path(args.output_dir or (Path(HERE) / "models" / "adjpx"))
    if not args.verify_only:
        destination.mkdir(parents=True, exist_ok=True)
    results = []
    for seed in args.seeds:
        # Preserve the full rolling window while giving each ensemble member a
        # deterministic row order.  XGBoost's histogram builder can produce
        # different trees from the same rows in a different stable order.
        order = np.random.RandomState(seed).permutation(len(train_frame))
        model = XGBRegressor(
            n_estimators=3000,
            objective="reg:squarederror",
            random_state=seed,
            early_stopping_rounds=50,
            n_jobs=-1,
        )
        model.fit(X_train[order], y_train[order], eval_set=[(X_valid, y_valid)], verbose=False)
        prediction = model.predict(X_valid)
        rank_ic, top3_target = daily_metrics(valid_frame.index, y_valid, prediction)
        result = {
            "seed": seed,
            "best_iteration": int(model.best_iteration),
            "best_score": float(model.best_score),
            "validation_rank_ic": rank_ic,
            "validation_top3_target_mean": top3_target,
        }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not args.verify_only:
            path = destination / f"{args.quarter}_regq_seed{seed}.json"
            model.get_booster().save_model(path)
    report = {
        "quarter": args.quarter,
        "feature_cutoff": str(feature_cutoff.date()),
        "label_day": str(label_day.date()),
        "train_start": str((start - pd.DateOffset(years=5)).date()),
        "validation_start": str((start - pd.DateOffset(years=1)).date()),
        "removed_last_train_day": str(removed_train_day.date()),
        "removed_last_validation_day": str(removed_valid_day.date()),
        "train_rows": len(train_frame),
        "validation_rows": len(valid_frame),
        "features": columns,
        "ensemble_row_order": "numpy.RandomState(seed).permutation",
        "results": results,
    }
    if not args.verify_only:
        (destination / f"{args.quarter}_training.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", required=True)
    parser.add_argument("--feature-cutoff")
    parser.add_argument("--label-day")
    parser.add_argument("--online-label", action="store_true")
    parser.add_argument("--seeds", nargs="+", type=int, default=list(SEEDS))
    parser.add_argument("--output-dir")
    parser.add_argument("--verify-only", action="store_true")
    return parser


if __name__ == "__main__":
    train(build_parser().parse_args())
