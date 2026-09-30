"""Preview-only CLI for stages 1 and 2; it never sends orders or saves positions."""
import argparse
import json
import math
import os
from pathlib import Path

import pandas as pd

from . import chip_data as C
from . import features_build as F
from . import price_data as PX
from . import strategy as S
from .config import HERE, SHARES_UNIT, START
from .model_adapter import Model
from .universe import pool_tickers
from .validation import parse_date, validate_prices

DEFAULT_REPORT_DIR = Path(HERE).parent / "reports"


def show(plan):
    print("\n" + "=" * 68)
    print(f"交易計畫預覽  資料截止 {plan['as_of']}  模型 {plan['model_version']}")
    print(f"價格來源 {plan['price_source']}  資料檢查 {plan['data_quality']['status']}")
    print(f"總資產估值 {plan['equity']:,.0f}  現金假設 {plan['cash']:,.0f} ({plan['cash_source']})")
    print(f"持倉來源 {plan['holdings_source']}；本計畫不可直接送單。")
    print("=" * 68)
    for sell in plan["sells"]:
        print(f"SELL {sell['code']} {sell.get('shares')} {plan['quantity_unit']} "
              f"原因 {sell['reason']} 參考價 {sell['ref_price']}")
    for hold in plan["holds"]:
        print(f"HOLD {hold['code']} 排名 {hold['rank']} {hold.get('shares')} {plan['quantity_unit']}")
    for buy in plan["buys"]:
        print(f"BUY  {buy['code']} 排名 {buy['rank']} 分數 {buy['score']} "
              f"{buy['shares']} {buy['unit']} 約 {buy['amount']:,.0f} 參考價 {buy['ref_price']}")
        print(f"     停損參考 {buy['stop_loss']} 停利參考 {buy['take_profit']} ATR14 {buy['atr14']}")
    for item in plan["skipped_unaffordable"]:
        print(f"SKIP {item['code']} {item['why']}")
    for item in plan["filtered_by_liquidity"]:
        print(f"FILTER {item['code']} {item['why']}")
    for warning in plan["limitations"]:
        print(f"[NOTE] {warning}")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="完整交易日 YYYY-MM-DD；預設今天，不會退回舊日期")
    parser.add_argument("--quarter", default=None, help="可選季度覆寫，必須與 --date 相符")
    parser.add_argument("--cash", type=float, default=None, help="預覽用現金假設，非自動查詢餘額")
    parser.add_argument("--offline", "--no-refresh", dest="offline", action="store_true",
                        help="完全離線，只讀既有價格及籌碼快取")
    parser.add_argument("--query-account", action="store_true",
                        help="明確要求查詢持股，只回報是否有資料，不納入持倉計算")
    parser.add_argument("--state", default=None, help="明確指定預覽用持倉 JSON；預設假設空手")
    parser.add_argument("--json", default=None, help="輸出路徑；預設 reports/xgb38_<source>_<date>.json")
    return parser


def _validate_args(args):
    if args.offline and args.query_account:
        raise ValueError("--offline 不可與 --query-account 同時使用")
    if args.cash is not None and (not math.isfinite(args.cash) or args.cash < 0):
        raise ValueError("--cash 必須是非負且有限的數字")
    return parse_date(args.date or pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d"))


def run(args):
    day = _validate_args(args)
    # Fail before expensive downloads if this quarter has no models.
    model = Model(day, quarter=args.quarter)
    tickers = pool_tickers()
    end = day.strftime("%Y-%m-%d")
    print(f"[RUN] {len(tickers)} 檔；{model.version}；資料日 {end}；"
          f"{'離線' if args.offline else '更新價格與籌碼'}", flush=True)
    stock = PX.load_pool(tickers, end, refresh=not args.offline)
    validate_prices(stock, tickers, day)
    if not args.offline:
        C.download_pool(tickers, START, end)
    X = F.build(stock, as_of=day, tickers=tickers)
    daily = F.latest_row(X, day)
    rank = model.rank(daily)

    account_status = "not_requested"
    if args.query_account:
        account = os.environ.get("STOCK_ACCOUNT", "")
        password = os.environ.get("STOCK_PASSWORD", "")
        if not account or not password:
            raise ValueError("--query-account 需要 STOCK_ACCOUNT 與 STOCK_PASSWORD")
        from stock_api import Get_User_Stocks
        raw = Get_User_Stocks(account, password)
        account_status = "data_received_not_reconciled" if raw else "empty_or_failed"
        print(f"[ACCOUNT] {account_status}；持股格式尚未對接，未納入計畫")

    state = {}
    holdings_source = "假設空手；未讀取帳戶持倉"
    if args.state:
        if not Path(args.state).is_file():
            raise ValueError("--state 檔案不存在")
        state = S.load_state(args.state)
        if not isinstance(state, dict):
            raise ValueError("--state 必須是持倉 dict")
        # Legacy state does not record its unit; any ambiguity stays explicit.
        holdings_source = f"使用者提供的預覽持倉；數量按 {SHARES_UNIT} 解讀，未對帳"
        for code, position in state.items():
            if not isinstance(position, dict):
                raise ValueError(f"持倉 {code} 格式錯誤")
            shares = position.get("shares")
            if not isinstance(shares, (int, float)) or not math.isfinite(shares) or shares < 0:
                raise ValueError(f"持倉 {code} 數量無效")

    cash = args.cash if args.cash is not None else 1_000_000.0
    plan = S.make_plan(rank, stock, state, cash, day)
    # Proposed next_state is NOT confirmed holdings and must not be saved as such.
    plan.pop("next_state", None)
    quality = dict(X.attrs["data_quality"])
    quality["status"] = "passed_with_nan" if quality["nan_features_by_ticker"] else "passed"
    plan.update({
        "schema_version": 1,
        "mode": "preview",
        "executable": False,
        "requested_date": end,
        "model_version": model.version,
        "model_quarter": model.quarter,
        "price_source": "adjpx",
        "quantity_unit": "張" if SHARES_UNIT == "lot" else "股",
        "offline": args.offline,
        "cash_source": "使用者指定，未驗證帳戶餘額" if args.cash is not None else "預設假設 1,000,000，未查詢餘額",
        "holdings_source": holdings_source,
        "account_query_status": account_status,
        "data_quality": quality,
        "ranking": [{"ticker": ticker, "score": float(row["score"]), "rank": int(row["rank"])}
                    for ticker, row in rank.iterrows()],
        "limitations": [
            "僅供預覽；未送單、未更新持倉，不代表成交或可執行委託。",
            "CLI 預覽保留原策略估算，不會查詢實際庫存、成交狀態或可用現金。",
            "參考價取自模型價格來源；adjpx 為還原價，不可直接作為實際委託價格。",
            "籌碼維持 chip_shift=0；歷史發布時間未逐項驗證，不宣稱無前視偏誤。",
        ],
    })
    print("\n[排名前 15]")
    for item in plan["ranking"][:15]:
        print(f"  {item['rank']:>2}. {item['ticker']} {item['score']:.5f}")
    show(plan)
    output = Path(args.json) if args.json else DEFAULT_REPORT_DIR / f"xgb38_adjpx_{end}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    print(f"[SAVED] {output.resolve()}")
    return plan


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.exit(1, f"[ERROR] {exc}\n")


if __name__ == "__main__":
    main()
