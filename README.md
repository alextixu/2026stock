# 期末stock_project 
模型接入與預覽操作請見 [MODEL_INTEGRATION.md](MODEL_INTEGRATION.md)。
模型預覽入口為 `run_model_strategy.py`；`main.py` 只是不交易的股價資料範例。

可至main.py測試功能

## 每晚自動計算明日操作（只算不下單）

- `install_nightly_task.ps1`：註冊 Windows 排程 `XGB38-NightlyPlan`，每天 21:00 執行 `run_nightly_plan.ps1`。
- 每晚以當日收盤資料計算下一個交易日的買進／賣出／續抱，沒有動作時顯示「明日不動作」；結果寫入 `reports/gui/<帳戶>/latest_plan.json`，紀錄在 `nightly_status.json` 與 `nightly.log`。
- 週末略過；休市或資料未更新時記錄失敗原因，可在 GUI 按「更新資料並計算」重試。
- **不會自動送單**：預約仍需在 GUI 勾選、按送出並確認。
- GUI 右上「本次買進金額」：輸入這次想投入的金額，按「套用金額」後依比例分配到勾選的買進股票（預設平均）；選中某列可在右側改「比例」（例如 50 / 30 / 20），張數即時重算，不需重跑模型。金額超過可用預算時改用可用預算；每晚計算也以此金額為上限。
- 移除排程：`Unregister-ScheduledTask -TaskName 'XGB38-NightlyPlan'`

## 每晚 21:00 自動送出明日操作

- `run_nightly_execute.ps1` 是無人值守的自動交易入口。
- 流程為：查 API 庫存 → 更新資料 → 產生計畫 → 送單前重查庫存 → 送出全部計畫委託。
- 賣出數量必須等於 API 當下庫存，不從預約紀錄推算。
- 任一筆失敗、庫存改變、資料不完整、模型缺少或重複委託都會停止後續送出。
- 執行紀錄寫入 `reports/gui/<帳戶>/nightly_status.json` 與 `nightly.log`。
- 已包含 2026Q1～Q4 的 adjpx 模型；季度不符或任一 seed 檔缺少時，流程會安全失敗，不會改用其他季度模型送單。

## 取得股票資訊
台股資料抓取工具，支援：
- TWSE（上市）
- TPEX（上櫃）
- ESB（興櫃）

### 安裝

```bash
pip install -r requirements.txt
```

### 使用方式

```python
from stock_api import get_taiwan_stock_data

# 取得股票資訊
# Input:
#   stock_code: 股票ID
#   start_date: 開始日期，YYYY-MM-DD
#   stop_date: 結束日期，YYYY-MM-DD
# Output: 股票資訊

df = get_taiwan_stock_data("2330", "2024-01-01", "2024-03-31")
```

### 輸出欄位

📊 資料欄位說明

新版資料介面維持舊版 API 的欄位名稱與資料語意，包含：

- date：日期
- capacity：成交股數
- turnover：成交金額（元）
- high：最高價
- low：最低價
- close：收盤價
- change：漲跌價差
- transaction_volume：成交筆數
- stock_code_id：股票代號
- open：開盤價
- market：資料市場（TWSE / TPEX / ESB）
- close_is_proxy：close 是否為代理值；興櫃為 True

為了方便 pandas 分析與模型訓練，資料型態改為：

- date 使用 datetime
- 數值欄位使用 numeric（int / float）
- 不再使用字串格式數值

### 注意事項

- TWSE / TPEX 使用官方標準 OHLC
- ESB 無官方標準 Open / Close
- ESB 的 `close` 為兩種成交方式合併後的加權平均成交價代理值
- ESB 沒有標準開盤價，因此 `open` 為 NaN

## 取得持有股票

### 使用方式

```python
# 取得持有股票
# Input:
#   account: 使用者帳號
#   password: 使用者密碼
# Output: 持有股票陣列

user_stocks = Get_User_Stocks(account, password)
```

## 預約購入股票

### 使用方式

```python
# 預約購入股票
# Input:
#   account: 使用者帳號
#   password: 使用者密碼
#   stock_code: 股票ID
#   stock_shares: 購入張數
#   stock_price: 購入價格
# Output: 是否成功預約購入(True/False)

Buy_Stock(account, password, "2330",1, 1975)
```

## 預約售出股票

### 使用方式

```python
# 預約售出股票
# Input:
#   account: 使用者帳號
#   password: 使用者密碼
#   stock_code: 股票ID
#   stock_shares: 售出張數
#   stock_price: 售出價格
# Output: 是否成功預約售出(True/False)

Sell_Stock(account, password, "2330",1, 1978)
```
