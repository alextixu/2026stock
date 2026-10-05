"""21:00 unattended flow: prepare tomorrow's plan and submit every order.

This is intentionally separate from ``nightly_plan.py``.  The preview job and
GUI remain non-executing unless the user explicitly submits from the GUI.
"""
from datetime import datetime

from nightly_plan import summarize
from trading_backend import TZ, TradingService, default_dates, now_text, write_json


def run_auto_execute(now=None, service=None):
    now = now or datetime.now(TZ)
    service = service or TradingService()
    ledger = service.ledger
    signal, target = default_dates(now)
    status = {
        "mode": "auto_execute",
        "started_at": now.isoformat(timespec="seconds"),
        "signal_date": signal,
        "target_date": target,
        "buy_amount": ledger.buy_amount,
    }
    with open(ledger.nightly_log_path, "a", encoding="utf-8") as log_file:
        def log(message):
            log_file.write(f"[{now_text()}] {message}\n")
            log_file.flush()

        log(f"=== 每晚自動執行：資料日 {signal} → 操作日 {target} ===")
        if now.weekday() >= 5:
            status.update(result="skipped", message="週末不計算、不送單")
        else:
            try:
                plan = service.prepare(target, signal, refresh=True, log=log)
                summary = summarize(plan)
                orders = [dict(order) for order in plan["orders"]]
                if not orders:
                    status.update(result="ok", summary=summary, message=summary["text"], submitted=[])
                else:
                    rows = service.submit(plan, orders, log=log)
                    keys = {(target, order["side"], order["code"]) for order in orders}
                    latest = {}
                    for row in rows:  # Ledger.rows() is newest first.
                        key = (row["target_date"], row["side"], row["code"])
                        if key in keys and key not in latest:
                            latest[key] = row
                    submitted = list(latest.values())
                    states = {row["status"] for row in submitted}
                    result = "ok" if states == {"accepted"} else "attention"
                    status.update(result=result, summary=summary, submitted=submitted,
                                  message=f"{summary['text']}；送出狀態 {', '.join(sorted(states))}")
            except Exception as exc:
                status.update(result="failed", message=f"{type(exc).__name__}：{exc}"[:500])
        status["finished_at"] = now_text()
        log(f"結果：{status['result']}｜{status['message']}")
    write_json(ledger.nightly_path, status)
    return status


if __name__ == "__main__":
    run_auto_execute()
