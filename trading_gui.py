"""Desktop dashboard for next-session simulated stock reservations."""
import argparse
import contextlib
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from nightly_plan import summarize
from trading_backend import (BUDGET_BUFFER, STATUS_NAMES, TradingService, allocate, default_dates,
                             fingerprint, normalize_holdings, write_json)

BG = "#f1f5fa"
NAVY = "#10243a"
BLUE = "#2563eb"
INK = "#172b43"
MUTED = "#62748a"
GREEN = "#087f63"


class LogWriter:
    def __init__(self, send):
        self.send, self.pending = send, ""

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.strip():
                self.send(line)
        return len(text)

    def flush(self):
        pass


class TradingApp:
    def __init__(self, root, target_date, signal_date):
        self.root = root
        self.service = TradingService()
        self.events = queue.Queue()
        self.busy = False
        self.sending = False
        self.plan = None
        self.raw_holdings = None
        self.holdings_verified = False
        self.order_rows = {}
        self.selected = set()
        self.target_date = tk.StringVar(value=target_date)
        self.signal_date = tk.StringVar(value=signal_date)
        self.status = tk.StringVar(value="啟動中…")
        self.selected_text = tk.StringVar(value="尚無操作計畫")
        self.total_text = tk.StringVar(value="—")
        self.remaining_text = tk.StringVar(value="—")
        self.edit_weight = tk.StringVar()
        self.weights = {}
        self.edit_price = tk.StringVar()
        self.amount = tk.StringVar(value=f"{self.service.ledger.buy_amount:,.0f}")
        self.plan_mtime = None
        root.title("XGB38 模擬交易工作台｜明日操作與預約")
        width, height = min(1420, root.winfo_screenwidth() - 60), min(900, root.winfo_screenheight() - 80)
        root.geometry(f"{width}x{height}+25+25")
        root.minsize(1120, 720)
        root.configure(bg=BG)
        root.option_add("*Font", ("Microsoft JhengHei UI", 10))
        self.style()
        self.layout()
        self.target_date.trace_add("write", lambda *_: self.update_summary())
        self.signal_date.trace_add("write", lambda *_: self.update_summary())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        root.after(300, self.startup)

    def style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TButton", font=("Microsoft JhengHei UI", 10), padding=(12, 8))
        style.configure("Accent.TButton", background=BLUE, foreground="white", padding=(12, 13),
                        font=("Microsoft JhengHei UI", 12, "bold"))
        style.map("Accent.TButton", background=[("disabled", "#bac7da"), ("active", "#1d4ed8")])
        style.configure("Treeview", font=("Microsoft JhengHei UI", 10), rowheight=32,
                        background="white", fieldbackground="white", foreground=INK, borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft JhengHei UI", 10, "bold"),
                        background="#e6edf6", foreground=INK, padding=(6, 10))
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", INK)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Microsoft JhengHei UI", 10, "bold"))

    def label(self, parent, text, size=10, color=INK, bg="white", bold=False, **kwargs):
        return tk.Label(parent, text=text, font=("Microsoft JhengHei UI", size, "bold" if bold else "normal"),
                        fg=color, bg=bg, **kwargs)

    def layout(self):
        header = tk.Frame(self.root, bg=NAVY, height=80)
        header.pack(fill="x")
        self.label(header, "XGB38  /  模擬交易工作台", 21, "white", NAVY, True).pack(side="left", padx=24, pady=20)
        self.label(header, "第一天起始資產  NT$ 100,000,000", 12, "#bdd1e8", NAVY).pack(side="right", padx=24)
        cards = tk.Frame(self.root, bg=BG)
        cards.pack(fill="x", padx=20, pady=(16, 12))
        self.card_values = {}
        for index, (key, title, value) in enumerate([
                ("initial", "期初資產｜2026/09/30", "100,000,000"),
                ("available", "可用預算｜扣除已預約", "100,000,000"),
                ("holdings", "目前持股｜API 查詢", "查詢中"),
                ("target", "操作目標｜09:00 早盤", self.target_date.get())]):
            cards.columnconfigure(index, weight=1)
            box = tk.Frame(cards, bg="white", padx=16, pady=10)
            box.grid(row=0, column=index, sticky="nsew", padx=(0, 10 if index < 3 else 0))
            self.label(box, title, 9, MUTED).pack(anchor="w")
            field = self.label(box, value, 20, INK, bold=True)
            field.pack(anchor="w", pady=(4, 0))
            self.card_values[key] = field
        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=20, pady=(0, 12))
        self.label(controls, "操作日", bg=BG).pack(side="left")
        self.target_entry = ttk.Entry(controls, textvariable=self.target_date, width=13)
        self.target_entry.pack(side="left", padx=(6, 14))
        self.label(controls, "資料截止日", bg=BG).pack(side="left")
        self.signal_entry = ttk.Entry(controls, textvariable=self.signal_date, width=13)
        self.signal_entry.pack(side="left", padx=(6, 16))
        self.calculate_button = ttk.Button(controls, text="更新資料並計算", command=lambda: self.calculate(True))
        self.calculate_button.pack(side="left", padx=4)
        self.offline_button = ttk.Button(controls, text="用快取重新計算", command=lambda: self.calculate(False))
        self.offline_button.pack(side="left", padx=4)
        self.holdings_button = ttk.Button(controls, text="查詢目前持股", command=self.refresh_holdings)
        self.holdings_button.pack(side="left", padx=4)
        self.amount_button = ttk.Button(controls, text="套用金額", command=self.save_amount)
        self.amount_button.pack(side="right")
        self.amount_entry = ttk.Entry(controls, textvariable=self.amount, width=15)
        self.amount_entry.pack(side="right", padx=(6, 6))
        self.amount_entry.bind("<Return>", lambda _event: self.save_amount())
        self.label(controls, "本次買進金額", bg=BG).pack(side="right")
        self.mapping_button = ttk.Button(controls, text="持股欄位設定", command=self.configure_holdings)
        self.mapping_button.pack(side="right", padx=(0, 18))
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=20)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=310)
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.plan_label = self.label(left, "等待載入明日操作計畫", 12, INK, BG, True, anchor="w")
        self.plan_label.pack(fill="x")
        self.nightly_label = self.label(left, "每晚 21:00 自動計算明日操作（只算不下單）", 9, MUTED, BG, anchor="w")
        self.nightly_label.pack(fill="x", pady=(2, 8))
        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill="both", expand=True)
        self.orders = self.table("明日操作", [("selected", "選取", 48), ("side", "操作", 60),
                ("code", "代號", 65), ("name", "股票", 105), ("weight", "比例", 60), ("lots", "張數", 65),
                ("price", "委託價格", 90), ("amount", "預約金額", 125), ("status", "狀態", 160)])
        self.orders.bind("<ButtonRelease-1>", self.order_click)
        self.orders.bind("<<TreeviewSelect>>", self.order_focus)
        self.rankings = self.table("全池排名", [("rank", "排名", 55), ("code", "代號", 80),
                ("name", "股票", 160), ("score", "模型相對分數", 150)])
        self.holdings_frame = ttk.Frame(self.tabs)
        self.tabs.add(self.holdings_frame, text="目前持股")
        self.holdings_note = self.label(self.holdings_frame, "尚未查詢", color=MUTED, bg=BG, anchor="w")
        self.holdings_note.pack(fill="x", pady=8, padx=8)
        self.holdings_tree = ttk.Treeview(self.holdings_frame, show="headings")
        self.holdings_tree.pack(fill="both", expand=True)
        self.history = self.table("預約紀錄", [("date", "目標日", 100), ("side", "操作", 60),
                ("code", "股票", 75), ("lots", "張數", 55), ("price", "預約價格", 90),
                ("status", "服務結果", 170), ("message", "API 訊息", 220)])
        self.history.bind("<Double-1>", self.show_record)
        history_bar = tk.Frame(self.history.master, bg=BG)
        history_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=6)
        self.cancel_button = ttk.Button(history_bar, text="標記選取紀錄為已刪單", command=self.mark_cancelled)
        self.cancel_button.pack(side="left")
        self.label(history_bar, "在交易服務刪單後使用：只更新本地帳本、釋放預算，該股票可重新預約", 9, MUTED, BG).pack(
            side="left", padx=10)

        right = tk.Frame(body, bg="white", padx=18, pady=18)
        right.grid(row=0, column=1, sticky="nsew")
        self.label(right, "送出預約", 18, bold=True).pack(anchor="w")
        self.label(right, "勾選左側操作；買進金額依比例分配成張數", 9, MUTED).pack(anchor="w", pady=(4, 14))
        tk.Label(right, textvariable=self.selected_text, bg="white", fg=INK, justify="left",
                 wraplength=270, font=("Microsoft JhengHei UI", 11)).pack(anchor="w")
        ttk.Separator(right).pack(fill="x", pady=12)
        self.label(right, "選中列的委託設定", 10, bold=True).pack(anchor="w")
        fields = tk.Frame(right, bg="white")
        fields.pack(fill="x", pady=8)
        self.label(fields, "比例（買進）").grid(row=0, column=0, sticky="w")
        self.weight_entry = ttk.Entry(fields, textvariable=self.edit_weight, width=11)
        self.weight_entry.grid(row=1, column=0, padx=(0, 12), sticky="w")
        self.label(fields, "每股委託價").grid(row=0, column=1, sticky="w")
        self.price_entry = ttk.Entry(fields, textvariable=self.edit_price, width=13)
        self.price_entry.grid(row=1, column=1, sticky="w")
        self.apply_button = ttk.Button(right, text="套用到選中列", command=self.apply_edit)
        self.apply_button.pack(fill="x", pady=(0, 8))
        self.label(right, "買進預約金額", 9, MUTED).pack(anchor="w")
        tk.Label(right, textvariable=self.total_text, bg="white", fg=INK,
                 font=("Microsoft JhengHei UI", 21, "bold")).pack(anchor="w", pady=(2, 8))
        tk.Label(right, textvariable=self.remaining_text, bg="white", fg=MUTED, justify="left",
                 wraplength=270, font=("Microsoft JhengHei UI", 9)).pack(anchor="w")
        self.submit_button = ttk.Button(right, text="送出選取的預約 →", style="Accent.TButton", command=self.submit)
        self.submit_button.pack(fill="x", pady=(16, 10))
        self.label(right, "只在按下送出並確認後呼叫 API。\n預約成功不等於已成交。\n目標日供計畫；實際排程依服務。", 9,
                   MUTED, justify="left", wraplength=270).pack(anchor="w")

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=20, pady=(12, 0))
        self.label(bottom, "執行訊息", 9, MUTED, BG).pack(anchor="w")
        self.log_box = tk.Text(bottom, height=4, bg="#e7edf5", fg=INK, relief="flat",
                               font=("Microsoft JhengHei UI", 9), padx=10, pady=5, state="disabled")
        self.log_box.pack(fill="x", pady=(4, 6))
        tk.Label(self.root, textvariable=self.status, bg=NAVY, fg="white", anchor="w", padx=20,
                 font=("Microsoft JhengHei UI", 9)).pack(fill="x", ipady=6)
        self.update_summary()

    def table(self, title, columns):
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=[column[0] for column in columns], show="headings", selectmode="browse")
        for name, label, width in columns:
            tree.heading(name, text=label)
            tree.column(name, width=width, minwidth=45, anchor="w", stretch=name in {"name", "status", "message"})
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return tree

    def log(self, text):
        self.events.put(("log", str(text)))

    def start_job(self, function, kind, sending=False):
        if self.busy:
            return
        self.busy, self.sending = True, sending
        self.status.set("送出預約中，請勿關閉…" if sending else "處理中，畫面可繼續檢視…")
        self.update_summary()

        def worker():
            try:
                with contextlib.redirect_stdout(LogWriter(self.log)):
                    value = function()
                self.events.put((kind, value))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log_box.configure(state="normal")
                self.log_box.insert("end", value + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
                continue
            self.busy, self.sending = False, False
            if kind == "plan":
                self.display_plan(value)
                self.display_holdings(value["holdings"])
                self.status.set("計畫已就緒，尚未送出任何新預約。")
            elif kind == "holdings":
                self.display_holdings(value)
                self.status.set("持股查詢成功。")
            elif kind == "submitted":
                self.refresh_history()
                self.status.set("送出流程結束；請查看『預約紀錄』，預約不等於成交。")
                self.tabs.select(3)
            elif kind == "error":
                self.refresh_history()
                self.log(value)
                self.status.set("未完成：" + value.splitlines()[0][:100])
                messagebox.showerror("未完成", value[:2500], parent=self.root)
            self.update_summary()
        self.watch_plan()
        self.root.after(120, self.poll)

    def watch_plan(self):
        """Reload the plan the 21:00 job wrote, unless the user is mid-operation."""
        path = self.service.ledger.plan_path
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == self.plan_mtime or self.busy:
            return
        self.plan_mtime = mtime
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return
        if self.plan and plan.get("created_at") == self.plan.get("created_at"):
            return
        self.display_plan(plan)
        self.show_nightly()
        self.log(f"已載入 {plan['created_at'][:16].replace('T', ' ')} 計算的 {plan['target_date']} 計畫。")
        self.status.set("已載入新計畫，尚未送出任何新預約。")

    def startup(self):
        path = self.service.ledger.plan_path
        if path.exists():
            try:
                self.plan_mtime = path.stat().st_mtime
                self.display_plan(json.loads(path.read_text(encoding="utf-8")))
                self.log("已載入最近一次計畫；重新查詢 API 持股後才啟用送出。")
            except (ValueError, KeyError):
                self.log("既有計畫格式不符，請重新計算。")
        self.show_nightly()
        self.refresh_history()
        self.refresh_holdings()

    def show_nightly(self):
        path = self.service.ledger.nightly_path
        if not path.exists():
            self.nightly_label.configure(text="每晚 21:00 自動計算明日操作（只算不下單）｜尚無自動計算紀錄")
            return
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return
        result = {"ok": "成功", "failed": "失敗", "skipped": "略過"}.get(info.get("result"), "未知")
        when = info.get("finished_at", "")[:16].replace("T", " ")
        self.nightly_label.configure(text=f"上次自動計算 {when}：{result}｜{info.get('message', '')}"[:160],
                                     fg=GREEN if info.get("result") == "ok" else "#b45309")

    def save_amount(self):
        if self.busy:
            return
        try:
            self.service.ledger.set_buy_amount(self.amount.get().replace(",", "").strip())
        except ValueError as exc:
            messagebox.showerror("本次買進金額", str(exc) if "金額" in str(exc) else "請輸入有效金額", parent=self.root)
            return
        value = self.service.ledger.buy_amount
        self.amount.set(f"{value:,.0f}")
        self.status.set(f"本次買進金額設為 {value:,.0f}，已依比例重新分配張數。")
        if self.plan:
            self.render_orders()
        else:
            self.update_summary()

    def spend_amount(self):
        return min(self.service.ledger.buy_amount, self.service.ledger.available_budget())

    def reallocate(self):
        """Split the buy amount over checked, unsent BUY rows by their weights."""
        buys = {iid: row for iid, row in self.order_rows.items() if iid in self.selected and row["side"] == "BUY"}
        for iid, lots in allocate(buys, self.weights, self.spend_amount()).items():
            self.order_rows[iid]["lots"] = lots

    def share_text(self, iid):
        buys = [key for key, row in self.order_rows.items() if key in self.selected and row["side"] == "BUY"]
        total = sum(self.weights.get(key, 0) for key in buys)
        return f"{self.weights[iid] / total:.0%}" if iid in buys and total > 0 else "—"

    def calculate(self, refresh):
        target, signal = self.target_date.get().strip(), self.signal_date.get().strip()
        self.holdings_verified = False
        self.start_job(lambda: self.service.prepare(target, signal, refresh, self.log), "plan")

    def refresh_holdings(self):
        self.holdings_verified = False
        self.start_job(self.service.broker.holdings, "holdings")

    def display_holdings(self, raw):
        self.raw_holdings = raw
        self.holdings_verified = True
        self.holdings_tree.delete(*self.holdings_tree.get_children())
        if raw == [] or raw == {}:
            self.holdings_tree.configure(columns=["message"])
            self.holdings_tree.heading("message", text="API 查詢結果")
            self.holdings_tree.insert("", "end", values=["目前沒有持股。預約買進尚未成交前，不會列為持股。"])
            self.holdings_note.configure(text="查詢成功｜空持股清單")
            self.card_values["holdings"].configure(text="0 檔")
        elif isinstance(raw, list) and all(isinstance(row, dict) for row in raw):
            columns = list(dict.fromkeys(key for row in raw for key in row))
            self.holdings_tree.configure(columns=columns)
            for key in columns:
                self.holdings_tree.heading(key, text=key)
                self.holdings_tree.column(key, width=120)
            for row in raw:
                self.holdings_tree.insert("", "end", values=[str(row.get(key, "")) for key in columns])
            self.holdings_note.configure(text="顯示 API 原始欄位｜第一次有持股時，請確認『持股欄位設定』")
            self.card_values["holdings"].configure(text=f"{len(raw)} 筆")
        else:
            self.holdings_tree.configure(columns=["raw"])
            self.holdings_tree.heading("raw", text="API 原始資料（待確認格式）")
            self.holdings_tree.insert("", "end", values=[json.dumps(raw, ensure_ascii=False)])
            self.holdings_note.configure(text="資料格式待確認；不會自動推測數量單位")
            self.card_values["holdings"].configure(text="待確認")

    def display_plan(self, plan):
        self.plan = plan
        self.target_date.set(plan["target_date"])
        self.signal_date.set(plan["signal_date"])
        self.plan_label.configure(text=f"{plan['target_date']} 操作計畫  ·  {plan['signal_date']} 收盤訊號  ·  "
                                       f"{summarize(plan)['text']}")
        self.card_values["target"].configure(text=plan["target_date"])
        self.orders.delete(*self.orders.get_children())
        self.order_rows = {str(index): dict(row) for index, row in enumerate(plan["orders"])}
        self.selected = set(self.order_rows)
        self.weights = {iid: 1.0 for iid, row in self.order_rows.items() if row["side"] == "BUY"}
        for iid in self.order_rows:
            self.orders.insert("", "end", iid=iid)
        names = {row["ticker"].split(".")[0]: row.get("name", "") for row in plan["ranking"]}
        for hold in plan.get("holds", []):
            self.orders.insert("", "end", iid=f"hold-{hold['code']}", values=(
                "", "續抱", hold["code"], hold.get("name") or names.get(hold["code"], ""), "", hold.get("shares", ""),
                "", "", f"排名 {hold.get('rank', '—')}｜不需下單"))
        if not plan["orders"]:
            self.orders.insert("", "end", iid="no-action", values=(
                "", "不動作", "", "明日無需下單", "", "", "", "", summarize(plan)["text"]))
        self.rankings.delete(*self.rankings.get_children())
        for row in plan["ranking"]:
            self.rankings.insert("", "end", values=(row["rank"], row["ticker"].split(".")[0], row["name"], f"{row['score']:.5f}"))
        self.refresh_history()
        if self.order_rows:
            first = next(iter(self.order_rows))
            self.orders.selection_set(first)
            self.order_focus()

    def refresh_history(self):
        rows = self.service.ledger.rows()
        self.history.delete(*self.history.get_children())
        self.history_records = {}
        for row in rows:
            iid = self.history.insert("", "end", values=(row["target_date"], row["side"], row["code"], row["lots"],
                    f"{row['price']:,.2f}", STATUS_NAMES.get(row["status"], row["status"]), row["message"]))
            self.history_records[iid] = row
        live = [r for r in rows if r["status"] != "cancelled"]  # cancelled orders no longer block the stock
        self.order_statuses = {(r["target_date"], r["side"], r["code"]): r["status"] for r in live}
        records = {(r["target_date"], r["side"], r["code"]): r for r in live}
        for iid, row in self.order_rows.items():
            record = records.get((self.plan["target_date"], row["side"], row["code"]))
            if record:
                self.selected.discard(iid)
                # Show what was actually sent, not the plan's original sizing.
                row.update(lots=record["lots"], price=record["price"])
        self.render_orders()

    def render_orders(self):
        self.reallocate()
        for iid, row in self.order_rows.items():
            state = self.order_statuses.get((self.plan["target_date"], row["side"], row["code"]))
            idle = row["side"] == "BUY" and iid not in self.selected and state is None
            if state:
                note = STATUS_NAMES.get(state, state)
            elif iid in self.selected and row["lots"] <= 0:
                note = "金額不足 1 張"
            else:
                note = "尚未送出"
            self.orders.item(iid, values=("✓" if iid in self.selected else "—", "買進" if row["side"] == "BUY" else "賣出",
                row["code"], row["name"], self.share_text(iid), "—" if idle else row["lots"], f"{row['price']:,.2f}",
                "—" if idle else f"{row['lots'] * row['price'] * 1000:,.0f}", note))
        self.update_summary()

    def order_click(self, event):
        if self.busy:
            return
        iid = self.orders.identify_row(event.y)
        if iid in self.order_rows and self.orders.identify_column(event.x) == "#1":
            row = self.order_rows[iid]
            if (self.plan["target_date"], row["side"], row["code"]) in self.order_statuses:
                return
            if iid in self.selected:
                self.selected.remove(iid)
            else:
                self.selected.add(iid)
            self.render_orders()

    def order_focus(self, _event=None):
        selection = self.orders.selection()
        if selection and selection[0] in self.order_rows:
            row = self.order_rows[selection[0]]
            self.edit_weight.set(f"{self.weights[selection[0]]:g}" if row["side"] == "BUY" else "—")
            self.edit_price.set(f"{row['price']:.2f}")

    def apply_edit(self):
        if self.busy or not self.orders.selection() or self.orders.selection()[0] not in self.order_rows:
            return
        iid = self.orders.selection()[0]
        row = self.order_rows[iid]
        if (self.plan["target_date"], row["side"], row["code"]) in self.order_statuses:
            messagebox.showinfo("不可修改", "這筆已有預約紀錄，不可當作新單重送。", parent=self.root)
            return
        try:
            import math
            price = float(self.edit_price.get())
            weight = float(self.edit_weight.get().rstrip("%")) if row["side"] == "BUY" else None
            if not math.isfinite(price) or price <= 0 or (weight is not None and (not math.isfinite(weight) or weight <= 0)):
                raise ValueError()
            row["price"] = price
            if weight is not None:
                self.weights[iid] = weight
            self.render_orders()
        except ValueError:
            messagebox.showerror("輸入錯誤", "比例需為正數（例如 50 或 1），價格需為有效正數。", parent=self.root)

    def update_summary(self):
        available = self.service.ledger.available_budget()
        if hasattr(self, "card_values"):
            self.card_values["available"].configure(text=f"{available:,.0f}")
        chosen = [self.order_rows[iid] for iid in sorted(self.selected)]
        total = sum(row["lots"] * row["price"] * 1000 for row in chosen if row["side"] == "BUY")
        self.selected_text.set("\n".join(f"{'買' if r['side']=='BUY' else '賣'} {r['code']} {r['name']}  {r['lots']} 張" for r in chosen)
                               or "尚未勾選操作")
        self.total_text.set(f"NT$ {total:,.0f}")
        reserved = total * (1 + BUDGET_BUFFER)
        amount = self.service.ledger.buy_amount
        spend = self.spend_amount()
        lots_ok = all(row["lots"] > 0 for row in chosen)
        self.remaining_text.set(f"本次買進金額：{amount:,.0f}" + ("（超過可用預算，改用可用預算）" if spend < amount else "")
                                + f"\n含 0.2% 緩衝：{reserved:,.0f}｜分配後未用：{max(0.0, spend - reserved):,.0f}\n"
                                f"送出後預算剩餘：{available-reserved:,.0f}\n不預先使用尚未成交的賣出款"
                                + ("" if lots_ok else "\n有勾選列金額不足 1 張：請提高金額、比例或取消勾選"))
        ready = (bool(self.plan) and self.holdings_verified and not self.busy and bool(chosen) and lots_ok
                 and fingerprint(self.raw_holdings) == self.plan.get("holdings_fingerprint") and reserved <= available
                 and self.target_date.get() == self.plan["target_date"] and self.signal_date.get() == self.plan["signal_date"])
        if hasattr(self, "submit_button"):
            self.submit_button.configure(state="normal" if ready else "disabled")
            for button in [self.calculate_button, self.offline_button, self.holdings_button, self.mapping_button,
                           self.apply_button, self.amount_button, self.cancel_button]:
                button.configure(state="disabled" if self.busy else "normal")
            for entry in [self.target_entry, self.signal_entry, self.weight_entry, self.price_entry, self.amount_entry]:
                entry.configure(state="disabled" if self.busy else "normal")

    def submit(self):
        if self.busy or not self.plan:
            return
        orders = [dict(self.order_rows[iid]) for iid in sorted(self.selected)]
        details = "\n".join(f"{o['side']} {o['code']} {o['name']}  {o['lots']} 張 @ {o['price']:.2f}" for o in orders)
        text = (f"目標操作日：{self.plan['target_date']}\n資料日：{self.plan['signal_date']}\n\n{details}\n\n"
                "按下確認將立即呼叫模擬交易 API。\nAPI 沒有指定日期參數，實際排程依服務。\n預約成功不代表成交。")
        if messagebox.askokcancel("確認送出以上預約", text, parent=self.root):
            self.start_job(lambda: self.service.submit(self.plan, orders, self.log), "submitted", sending=True)

    def show_record(self, _event=None):
        selection = self.history.selection()
        if selection:
            row = self.history_records[selection[0]]
            messagebox.showinfo("預約明細", "\n".join(f"{key}: {value}" for key, value in row.items()), parent=self.root)

    def mark_cancelled(self):
        if self.busy:
            return
        selection = self.history.selection()
        if not selection:
            messagebox.showinfo("標記刪單", "請先在『預約紀錄』選一筆。", parent=self.root)
            return
        row = self.history_records[selection[0]]
        text = (f"{row['target_date']}  {row['side']} {row['code']} {row['name']}  {row['lots']} 張 @ {row['price']:.2f}\n\n"
                "請確認已在交易服務刪除這筆預約。\n這裡只更新本地帳本：釋放預算，且這檔可重新勾選送出。\n不會呼叫任何 API。")
        if not messagebox.askokcancel("標記為已刪單", text, parent=self.root):
            return
        try:
            self.service.ledger.cancel(row["id"])
        except ValueError as exc:
            messagebox.showerror("無法標記", str(exc), parent=self.root)
            return
        self.refresh_history()
        self.status.set(f"{row['code']} {row['name']} 已標記為已刪單，預算已釋放。")

    def configure_holdings(self):
        raw = self.raw_holdings
        if not isinstance(raw, list) or not raw or not all(isinstance(row, dict) for row in raw):
            messagebox.showinfo("持股欄位", "目前沒有可對應的持股列。API 有持股後，這裡會列出實際欄位供選擇。", parent=self.root)
            return
        keys = sorted(set.intersection(*(set(row) for row in raw)))
        dialog = tk.Toplevel(self.root)
        dialog.title("確認 API 持股欄位與單位")
        dialog.geometry("430x300")
        dialog.transient(self.root)
        current = self.service.ledger.mapping()
        values = {}
        for label, name, options in [("股票代號欄位", "code", keys), ("持股數量欄位", "quantity", keys),
                                     ("數量單位：lot=張 / share=股", "unit", ["lot", "share"])]:
            ttk.Label(dialog, text=label).pack(anchor="w", padx=20, pady=(12, 3))
            values[name] = tk.StringVar(value=current.get(name, ""))
            ttk.Combobox(dialog, textvariable=values[name], values=options, state="readonly", width=40).pack(padx=20)

        def save():
            mapping = {name: variable.get() for name, variable in values.items()}
            try:
                normalize_holdings(raw, mapping)
                write_json(self.service.ledger.mapping_path, mapping)
                self.holdings_verified = False
                self.update_summary()
                dialog.destroy()
                self.status.set("持股欄位已設定，請重新計算計畫。")
            except (ValueError, KeyError) as exc:
                messagebox.showerror("無法套用", str(exc), parent=dialog)
        ttk.Button(dialog, text="驗證並儲存", command=save).pack(pady=16)

    def close(self):
        if self.sending:
            messagebox.showinfo("正在送出", "請等待逐筆結果寫入；不會自動重送。", parent=self.root)
            return
        self.root.destroy()


def main():
    signal, target = default_dates()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default=target)
    parser.add_argument("--signal-date", default=signal)
    args = parser.parse_args()
    root = tk.Tk()
    TradingApp(root, args.target_date, args.signal_date)
    root.mainloop()


if __name__ == "__main__":
    main()
