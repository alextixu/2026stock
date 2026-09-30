# 模型整合與操作說明

完整架構見 [`xgb38_pack/架構說明.md`](xgb38_pack/架構說明.md)。

## 固定資料口徑

| 用途 | 資料 | 說明 |
|---|---|---|
| 模型特徵與排名 | yfinance `auto_adjust=True` | 還原 OHLCV，對應 `models/adjpx/` |
| 籌碼特徵 | FinMind | 法人、融資券、外資持股 |
| 委託參考價 | `stock_api.get_taiwan_stock_data()` | 未還原價，只用於實際交易計畫 |
| 庫存 | `stock_api.Get_User_Stocks()` | 賣出清倉的唯一數量來源 |
| 預約買賣 | `stock_api.Buy_Stock()` / `Sell_Stock()` | 只在 GUI 確認後呼叫 |

專案不再提供 `rawpx` 模型、`PRICE_SOURCE` 環境變數或 `--price-source` 參數。

## 環境變數

```powershell
$env:STOCK_ACCOUNT = '你的帳號'
$env:STOCK_PASSWORD = '你的密碼'
$env:FINMIND_TOKEN = '你的 FinMind token'
```

可選設定：

| 變數 | 用途 |
|---|---|
| `FINMIND_API_URL` | 覆寫 FinMind HTTPS endpoint |
| `FINMIND_DIR` | 覆寫籌碼快取目錄 |
| `XGB38_CACHE` | 覆寫模型資料快取根目錄 |
| `XGB38_QUARTER` | 覆寫模型季度，但必須與資料日期一致 |
| `SHARES_UNIT` | CLI 預覽數量單位；GUI 固定要求 `lot` |

## CLI 預覽

```powershell
python -B -X utf8 run_model_strategy.py --date 2026-09-24 --cash 3000000
```

離線使用現有快取：

```powershell
python -B -X utf8 run_model_strategy.py --offline --date 2026-09-24 --cash 3000000
```

CLI 會輸出 `reports/xgb38_adjpx_<date>.json`，且永遠不送單。`--execute` 會立即報錯。

## GUI

```powershell
.\launch_trading_gui.ps1
```

GUI 流程：

1. 查詢 API 目前庫存。
2. 用 adjpx 模型計算全池排名。
3. 另取 `stock_api` 未還原價建立委託計畫。
4. 顯示買進、賣出、續抱、張數與價格。
5. 使用者勾選並再次確認後才送出。
6. 送出前重查 API 庫存；庫存改變就拒絕舊計畫。
7. SELL 清倉張數必須等於當下 API 庫存，不從預約紀錄推算。

`reports/gui/<account-hash>/reservations.sqlite3` 是預約稽核與防重送帳本，不是庫存帳。

## 每晚計畫

`nightly_plan.py` 只調用 `TradingService.prepare()`，不調用 `submit()`。它可以產生明日計畫，但不會自動下單。

## 目前限制

- 只有 2026Q1、Q2、Q3 模型；Q4 日期會停止。
- 股票池是固定 CSV，不是歷史成分股快照。
- FinMind `chip_shift=0` 的發布時點仍需 point-in-time 驗證。
- 交易 API 只能回報預約是否成功，不能確認成交。
- API 無取消預約函數；GUI 的「已刪單」只更新本地紀錄。
- 預約價來自最後一個完整交易日，不是盤中即時報價。
