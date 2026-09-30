"""Isolated adapter around existing stock_api functions; no invented endpoints.

Credentials arrive through environment variables, never command arguments.
Only the GUI's explicit submit action invokes buy/sell modes.
"""
import contextlib
import io
import json
import os
import sys


def call(action, payload):
    import requests
    from stock_api import core

    account = os.environ.get("STOCK_ACCOUNT", "")
    password = os.environ.get("STOCK_PASSWORD", "")
    if not account or not password:
        return {"status": "blocked", "message": "尚未設定交易帳密環境變數"}

    class BoundedTransport:
        envelope = None

        def post(self, url, **kwargs):
            # Each worker is a fresh process: no shared module/network state.
            response = requests.post(url, timeout=(10, 25), **kwargs)
            response.raise_for_status()
            self.envelope = response.json()
            return response

    transport = BoundedTransport()
    original_transport = core.requests
    core.requests = transport
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            if action == "holdings":
                result = core.Get_User_Stocks(account, password)
            elif action in {"buy", "sell"}:
                function = core.Buy_Stock if action == "buy" else core.Sell_Stock
                result = function(account, password, payload["code"], payload["lots"], payload["price"])
            else:
                return {"status": "blocked", "message": "不支援的操作"}
        envelope = transport.envelope
        if not isinstance(envelope, dict) or "result" not in envelope:
            return {"status": "unknown", "message": "API 回應格式不符，請勿直接重送"}
        if envelope["result"] != "success":
            return {"status": "rejected", "message": str(envelope.get("status", "API 回報失敗"))[:500]}
        if action == "holdings":
            return {"status": "ok", "data": result}
        return {"status": "accepted", "message": str(envelope.get("status", "預約成功"))[:500]}
    except Exception as exc:
        return {"status": "unknown", "message": f"{type(exc).__name__}：無法確認服務結果；不自動重送"}
    finally:
        core.requests = original_transport


if __name__ == "__main__":
    request = json.loads(sys.stdin.read())
    print(json.dumps(call(request["action"], request.get("payload", {})), ensure_ascii=True))
