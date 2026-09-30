"""套件設定。"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# 選股固定使用 yfinance auto_adjust=True 的還原價與 adjpx 模型。
# 未還原價只用於建立實際委託計畫，由 stock_api 取得。
MODEL_DIR = os.path.join(HERE, "models", "adjpx")
CACHE_DIR = os.environ.get("XGB38_CACHE", os.path.join(HERE, "cache"))
CHIP_DIR = os.path.join(CACHE_DIR, "finmind")
STATE_FP = os.path.join(CACHE_DIR, "positions.json")

POOL_CSV = os.path.join(HERE, "universe_tw50.csv")
FEATURE_CSV = os.path.join(HERE, "feature_shortlist_v2.csv")

# 模型
SEEDS = (42, 43, 44)
QUARTER = os.environ.get("XGB38_QUARTER") or None
if QUARTER is not None and not re.fullmatch(r"\d{4}Q[1-4]", QUARTER):
    raise ValueError("XGB38_QUARTER 格式必須是 YYYYQ1～YYYYQ4")

# 交易規則
TOPK = 3
EXIT_RANK = 5
OUT_DAYS = 1
MAXRANK = 15
MIN_LOTS = 2000
ATR_MULT = 3.0
TAKE_PCT = 0.30
ATR_WIN = 14

# Buy_Stock / Sell_Stock 的 stock_shares 是張，不是股。
SHARES_UNIT = os.environ.get("SHARES_UNIT", "lot")
if SHARES_UNIT not in {"lot", "share"}:
    raise ValueError("SHARES_UNIT 必須是 lot 或 share")
LOT_SIZE = 1000

WARMUP_ROWS = 260
START = "2016-01-01"
