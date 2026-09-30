"""Nightly job (21:00): compute the next session's BUY / SELL / HOLD plan.

It only queries holdings and writes latest_plan.json; it never submits orders.
Reservations still require the GUI's explicit submit and confirmation.
"""
import contextlib
from datetime import datetime

from trading_backend import TZ, TradingService, default_dates, now_text, write_json


def summarize(plan):
    buys = [o for o in plan["orders"] if o["side"] == "BUY"]
    sells = [o for o in plan["orders"] if o["side"] == "SELL"]
    holds = plan.get("holds", [])
    if not buys and not sells:
        text = "明日不動作" + (f"（續抱 {len(holds)} 檔）" if holds else "（空手、無買進訊號）")
    else:
        parts = [f"買進 {len(buys)} 檔" if buys else "", f"賣出 {len(sells)} 檔" if sells else "",
                 f"續抱 {len(holds)} 檔" if holds else ""]
        text = "明日" + "、".join(part for part in parts if part)
    return {"buys": len(buys), "sells": len(sells), "holds": len(holds), "text": text}


def run_nightly(now=None, service=None):
    now = now or datetime.now(TZ)
    service = service or TradingService()
    ledger = service.ledger
    signal, target = default_dates(now)
    status = {"started_at": now.isoformat(timespec="seconds"), "signal_date": signal, "target_date": target,
              "buy_amount": ledger.buy_amount}
    with open(ledger.nightly_log_path, "a", encoding="utf-8") as log_file:
        def log(text):
            log_file.write(f"[{now_text()}] {text}\n")
            log_file.flush()

        log(f"=== 每晚計算：資料日 {signal} → 操作日 {target}；本次買進金額 {status['buy_amount']:,.0f} ===")
        if now.weekday() >= 5:
            status.update(result="skipped", message="週末不重新計算；沿用最近一個交易日的計畫")
        else:
            try:
                # The model prints progress; keep it in the log instead of a console (pythonw has none).
                with contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
                    plan = service.prepare(target, signal, refresh=True, log=log)
                status.update(result="ok", summary=summarize(plan), plan_created_at=plan["created_at"],
                              message=summarize(plan)["text"])
            except Exception as exc:
                status.update(result="failed", message=f"{type(exc).__name__}：{exc}"[:500],
                              hint="若今天休市或資料尚未更新，可稍後在 GUI 按『更新資料並計算』")
        status["finished_at"] = now_text()
        log(f"結果：{status['result']}｜{status['message']}")
    write_json(ledger.nightly_path, status)
    return status


if __name__ == "__main__":
    run_nightly()
