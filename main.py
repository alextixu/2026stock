"""最小可執行範例：只讀取股票資料，不查帳戶、不送出任何交易。"""
from stock_api import get_taiwan_stock_data


def main():
    frame = get_taiwan_stock_data("2330", "2026-01-01", "2026-03-31")
    columns = ["date", "open", "high", "low", "close", "market"]
    print(frame[columns].head(5).to_string(index=False))
    if frame.empty:
        print("指定期間沒有交易資料")
        return
    latest = frame.sort_values("date").iloc[-1]
    print(f"latest_trading_date: {latest['date'].date()}")
    print(f"latest_close: {latest['close']}")


if __name__ == "__main__":
    main()
