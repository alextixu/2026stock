"""Planning and persistent reservation ledger for the local simulated-trading GUI."""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
INITIAL_ASSETS = 100_000_000.0
FIRST_DAY = "2026-09-30"
BUDGET_BUFFER = 0.002  # Budget cushion, not a statement of the server's fee schedule.
STATUS_NAMES = {"submitting": "送出中／待確認", "accepted": "預約成功（未確認成交）",
                "rejected": "服務拒絕", "unknown": "結果待確認・勿重送", "not_sent": "未送出",
                "cancelled": "已刪單"}
ORDER_COLUMNS = """id INTEGER PRIMARY KEY, target_date TEXT, signal_date TEXT, side TEXT,
                code TEXT, name TEXT, lots INTEGER, price REAL, reserve REAL,
                status TEXT, message TEXT, created_at TEXT, updated_at TEXT"""


def now_text():
    return datetime.now(TZ).isoformat(timespec="seconds")


def next_session(day):
    """Next weekday; exchange holidays surface later as missing price data."""
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def default_dates(now=None):
    """(signal, target): today's close is used only after 15:00, when daily data exists."""
    now = now or datetime.now(TZ)
    signal = now.date() if now.hour >= 15 else now.date() - timedelta(days=1)
    while signal.weekday() >= 5:
        signal -= timedelta(days=1)
    return signal.isoformat(), next_session(signal).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class Broker:
    def request(self, action, payload=None):
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            interpreter = str(Path(sys.executable).with_name("python.exe")) if Path(sys.executable).name.lower() == "pythonw.exe" else sys.executable
            result = subprocess.run([interpreter, "-B", str(ROOT / "broker_worker.py")],
                                    input=json.dumps({"action": action, "payload": payload or {}}),
                                    capture_output=True, text=True, encoding="utf-8", timeout=45,
                                    cwd=ROOT, creationflags=creationflags)
            if result.returncode != 0:
                return {"status": "unknown", "message": "API 工作程序異常；請先核對結果"}
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            return {"status": "unknown", "message": "API 回應逾時或解析失敗；請勿重送"}

    def holdings(self):
        result = self.request("holdings")
        if result.get("status") != "ok":
            raise ValueError("持股查詢失敗：" + result.get("message", "未知狀態"))
        return result["data"]


def normalize_holdings(raw, mapping=None):
    if raw == []:
        return {}
    # An empty dictionary is accepted here only after the worker verified success.
    if raw == {}:
        return {}
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        raise ValueError("持股資料不是已確認的列格式；請檢視 API 原始資料，尚不自動交易")
    mapping = mapping or {}
    if not mapping.get("code") or not mapping.get("quantity") or mapping.get("unit") not in {"lot", "share"}:
        raise ValueError("有實際持股：請先用『持股欄位設定』指定代號、數量欄位與股／張單位")
    positions = {}
    for row in raw:
        code = str(row[mapping["code"]]).strip()
        if not re.fullmatch(r"[0-9A-Za-z]+", code):
            raise ValueError("持股代號格式不明")
        quantity = float(row[mapping["quantity"]])
        lots = quantity / 1000 if mapping["unit"] == "share" else quantity
        if not math.isfinite(lots) or lots < 0 or not lots.is_integer():
            raise ValueError(f"{code} 持股不是完整張數；目前不能自動處理零股")
        if lots == 0:
            continue
        if code in positions:
            raise ValueError(f"{code} 出現多筆持股，需先確認彙總語意")
        positions[code] = {"shares": int(lots), "entry": None, "sl": None, "tp": None,
                           "entry_date": None, "out_cnt": 0}
    return positions


class Ledger:
    def __init__(self, directory=None, account=None):
        account = account if account is not None else os.environ.get("STOCK_ACCOUNT", "unconfigured")
        self.account_key = hashlib.sha256(account.encode()).hexdigest()[:20]
        self.directory = Path(directory) if directory else ROOT / "reports" / "gui" / self.account_key
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "reservations.sqlite3"
        self.plan_path = self.directory / "latest_plan.json"
        self.mapping_path = self.directory / "holdings_mapping.json"
        self.nightly_path = self.directory / "nightly_status.json"
        self.nightly_log_path = self.directory / "nightly.log"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('initial_assets', ?)", (str(INITIAL_ASSETS),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('first_day', ?)", (FIRST_DAY,))
            db.execute(f"CREATE TABLE IF NOT EXISTS orders ({ORDER_COLUMNS})")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_autoindex_orders_1'").fetchone():
                # Older ledgers had a table-level UNIQUE; rebuild so cancelled rows can be kept as history.
                db.execute(f"CREATE TABLE orders_migrated ({ORDER_COLUMNS})")
                db.execute("INSERT INTO orders_migrated SELECT * FROM orders")
                db.execute("DROP TABLE orders")
                db.execute("ALTER TABLE orders_migrated RENAME TO orders")
            # One live order per day/side/stock; a cancelled one no longer blocks a new reservation.
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS orders_live_key
                          ON orders(target_date, side, code) WHERE status != 'cancelled'""")

    @contextlib.contextmanager
    def connect(self):
        # sqlite3's own context manager only commits; close so Windows releases the file.
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def initial_assets(self):
        with self.connect() as db:
            return float(db.execute("SELECT value FROM settings WHERE key='initial_assets'").fetchone()[0])

    def rows(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM orders ORDER BY id DESC")]

    @property
    def buy_amount(self):
        """Money the user wants to put into the next batch of buys; defaults to the initial assets."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='buy_amount'").fetchone()
        return float(row[0]) if row else self.initial_assets

    def set_buy_amount(self, value):
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("本次買進金額必須是正數")
        if value > self.initial_assets:
            raise ValueError(f"本次買進金額不可超過期初資產 {self.initial_assets:,.0f}")
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('buy_amount', ?)", (str(value),))

    def available_budget(self):
        with self.connect() as db:
            reserved = db.execute("SELECT COALESCE(SUM(reserve),0) FROM orders WHERE status IN ('accepted','submitting','unknown')").fetchone()[0]
        return max(0.0, self.initial_assets - reserved)

    def mapping(self):
        return json.loads(self.mapping_path.read_text(encoding="utf-8")) if self.mapping_path.exists() else {}

    def claim(self, plan, orders):
        """Reserve the whole batch before any HTTP call. Prevent double-click/restart repeats."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reserved = db.execute("SELECT COALESCE(SUM(reserve),0) FROM orders WHERE status IN ('accepted','submitting','unknown')").fetchone()[0]
            cost = sum(order["lots"] * order["price"] * 1000 * (1 + BUDGET_BUFFER)
                       for order in orders if order["side"] == "BUY")
            if cost > self.initial_assets - reserved + 0.001:
                raise ValueError("剩餘預算不足；未確認的賣出款不計入可用資金")
            ids = []
            try:
                for order in orders:
                    amount = order["lots"] * order["price"] * 1000 * (1 + BUDGET_BUFFER) if order["side"] == "BUY" else 0
                    cursor = db.execute("""INSERT INTO orders
                        (target_date,signal_date,side,code,name,lots,price,reserve,status,message,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        plan["target_date"], plan["signal_date"], order["side"], order["code"], order["name"],
                        order["lots"], order["price"], amount, "submitting", "已建立本地送出紀錄", now_text(), now_text()))
                    ids.append(cursor.lastrowid)
            except sqlite3.IntegrityError:
                raise ValueError("同日期、股票與方向已有紀錄；為避免重複預約，本次不送出") from None
            return ids

    def cancel(self, order_id):
        """Record that the user deleted this reservation at the service; releases its budget."""
        with self.connect() as db:
            row = db.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise ValueError("找不到這筆預約紀錄")
            if row["status"] not in {"accepted", "unknown"}:
                raise ValueError(f"狀態為『{STATUS_NAMES.get(row['status'], row['status'])}』的紀錄不需標記刪單")
            db.execute("UPDATE orders SET status='cancelled', reserve=0, message=?, updated_at=? WHERE id=?",
                       ("使用者已在交易服務刪單（本地標記）", now_text(), order_id))

    def update(self, order_id, status, message):
        with self.connect() as db:
            db.execute("UPDATE orders SET status=?,message=?,updated_at=? WHERE id=?", (status, message, now_text(), order_id))


def allocate(orders, weights, amount, buffer=BUDGET_BUFFER):
    """Split `amount` over BUY orders by weight; lots round down so the batch never exceeds it.

    orders: {key: order}; weights: {key: non-negative number}. Returns {key: lots} for BUY keys.
    """
    buys = {key: order for key, order in orders.items() if order["side"] == "BUY"}
    total = sum(max(0.0, float(weights.get(key, 0))) for key in buys)
    lots = {}
    for key, order in buys.items():
        share = max(0.0, float(weights.get(key, 0))) / total if total > 0 else 0.0
        lots[key] = int(amount * share / (1 + buffer) // (order["price"] * 1000))
    return lots


def validate_orders(plan, orders, positions):
    if not orders:
        raise ValueError("請先勾選要送出的操作")
    allowed = {(row["side"], row["code"]) for row in plan["orders"]}
    seen = set()
    for order in orders:
        key = (order["side"], order["code"])
        if key not in allowed or key in seen:
            raise ValueError("操作不在計畫中或有重複股票")
        seen.add(key)
        lots, price = order["lots"], order["price"]
        if isinstance(lots, bool) or not isinstance(lots, int) or lots <= 0:
            raise ValueError("API 數量必須是正整數『張』")
        if not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0:
            raise ValueError("委託價格必須是有效正數")
        if order["side"] == "SELL":
            inventory_lots = positions.get(order["code"], {}).get("shares", 0)
            if inventory_lots <= 0:
                raise ValueError(f"{order['code']} 目前庫存沒有可售張數")
            if lots != inventory_lots:
                raise ValueError(
                    f"{order['code']} 清倉張數必須等於 API 目前庫存 {inventory_lots} 張；"
                    "不使用預約紀錄推算庫存"
                )
    buys = {o["code"] for o in orders if o["side"] == "BUY"}
    sells = {o["code"] for o in orders if o["side"] == "SELL"}
    if buys & sells:
        raise ValueError("同一批次不得同股票買進又賣出")


class TradingService:
    def __init__(self, ledger=None, broker=None):
        self.ledger = ledger or Ledger()
        self.broker = broker or Broker()

    def prepare(self, target_date, signal_date, refresh=True, log=print):
        from stock_api import get_stock_info
        from xgb38_pack import run_strategy as R, strategy as S, price_data as PX
        from xgb38_pack.config import CACHE_DIR, SHARES_UNIT
        from xgb38_pack.validation import parse_date
        target, signal = parse_date(target_date), parse_date(signal_date)
        if signal >= target:
            raise ValueError("訊號日必須早於預計操作日")
        if (target - signal).days > 7:
            raise ValueError("訊號日距操作日超過七天，請重新選擇資料日")
        if SHARES_UNIT != "lot":
            raise ValueError("GUI 預約僅支援張數；請設定 SHARES_UNIT=lot")
        log("查詢目前持股…")
        raw = self.broker.holdings()
        positions = normalize_holdings(raw, self.ledger.mapping())
        budget = min(self.ledger.available_budget(), self.ledger.buy_amount)
        log("計算整池模型排名；可能需要約一分鐘…")
        args = R.build_parser().parse_args(["--date", signal_date, "--cash", str(budget),
                                           "--json", str(self.ledger.directory / "signals.json")]
                                          + ([] if refresh else ["--offline"]))
        preview = R.run(args)
        rank = pd.DataFrame(preview["ranking"]).set_index("ticker")
        needed = list(rank.head(15).index)
        for code in positions:
            candidates = [ticker for ticker in rank.index if ticker.split(".")[0] == code]
            if not candidates:
                raise ValueError(f"目前持股 {code} 不在模型股票池，請另行處理後再計算")
            if candidates[0] not in needed:
                needed.append(candidates[0])
        log(f"讀取 {len(needed)} 檔未還原價格，作為實際預約參考價…")
        raw_dir = Path(CACHE_DIR) / "execution_raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        prices = {}
        start = (signal - pd.Timedelta(days=50)).strftime("%Y-%m-%d")
        for index, ticker in enumerate(needed, 1):
            path = raw_dir / f"{ticker.replace('.', '_')}.parquet"
            frame = pd.read_parquet(path) if path.exists() else None
            if frame is None or frame.index.max() < signal:
                frame = PX.fetch_execution_price(ticker.split(".")[0], start, signal_date)
                frame = PX._normalize(frame)
                if not frame.empty:
                    frame.to_parquet(path)
            frame = frame.loc[frame.index <= signal]
            if len(frame) < 20 or frame.index.max() != signal or frame[PX.COLS].tail(20).isna().any().any():
                raise ValueError(f"{ticker} 的未還原交易價格不完整，暫不能建立預約")
            prices[ticker] = frame
            log(f"未還原價格 {index}/{len(needed)}：{ticker}")
        proposed = S.make_plan(rank, prices, positions, budget, signal,
                               conservative=True, budget_buffer=BUDGET_BUFFER)
        names = {}
        for ticker in rank.index:
            code = ticker.split(".")[0]
            try:
                names[code] = get_stock_info(code).get("name", code)
            except ValueError:
                names[code] = code
        orders = []
        for side, key in [("SELL", "sells"), ("BUY", "buys")]:
            for row in proposed[key]:
                orders.append({"side": side, "code": row["code"], "name": names.get(row["code"], row["code"]),
                               "lots": int(row["shares"]), "price": float(row["ref_price"]),
                               "rank": row.get("rank"), "score": row.get("score"),
                               "reason": row.get("reason", "模型排名與流動性符合"),
                               "stop_loss": row.get("stop_loss"), "take_profit": row.get("take_profit")})
        plan = {"target_date": target_date, "signal_date": signal_date, "created_at": now_text(),
                "initial_assets": self.ledger.initial_assets, "buy_amount": self.ledger.buy_amount,
                "available_budget": budget,
                "account_key": self.ledger.account_key, "holdings_fingerprint": fingerprint(raw),
                "holdings": raw, "normalized_holdings": positions, "orders": orders,
                "holds": [{**row, "name": names.get(row["code"], row["code"])} for row in proposed["holds"]],
                "skipped": proposed["skipped_unaffordable"],
                "ranking": [{**row, "name": names.get(row["ticker"].split(".")[0], "")} for row in preview["ranking"]],
                "model_version": preview["model_version"], "data_quality": preview["data_quality"],
                "price_basis": "stock_api 未還原收盤價；可在送出前調整", "budget_buffer": BUDGET_BUFFER,
                "schedule_note": "目標操作日僅供計畫；API 無日期參數，實際排程由服務決定"}
        write_json(self.ledger.plan_path, plan)
        return plan

    def submit(self, plan, orders, log=print):
        if plan.get("account_key") != self.ledger.account_key:
            raise ValueError("此計畫屬於另一個帳戶")
        target = datetime.fromisoformat(plan["target_date"]).date()
        today = datetime.now(TZ).date()
        if target not in {today, next_session(today)}:
            raise ValueError("只可送出今天或下一個交易日的計畫；請重新計算日期")
        raw = self.broker.holdings()
        if fingerprint(raw) != plan["holdings_fingerprint"]:
            raise ValueError("實際持股已改變，請先重新計算計畫")
        positions = normalize_holdings(raw, self.ledger.mapping())
        validate_orders(plan, orders, positions)
        orders = sorted(orders, key=lambda row: row["side"] != "SELL")
        ids = self.ledger.claim(plan, orders)
        for index, (order_id, order) in enumerate(zip(ids, orders)):
            log(f"送出 {order['side']} {order['code']} {order['lots']} 張 @ {order['price']}")
            result = self.broker.request(order["side"].lower(), order)
            status = result.get("status")
            if status not in {"accepted", "rejected"}:
                status = "unknown"
            self.ledger.update(order_id, status, result.get("message", "服務未提供說明"))
            log(f"{order['code']}：{STATUS_NAMES[status]}")
            if status != "accepted":
                for remaining in ids[index + 1:]:
                    self.ledger.update(remaining, "not_sent", "前一筆未成功，本筆沒有送到 API")
                break
        return self.ledger.rows()
