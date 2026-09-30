"""每個特徵需要的最少歷史列數(含當天)。回看期不足的列一律設為缺值,不用前值填補、也不讓指數平滑從第一天就出值。

  用法: feats = apply_lookback_mask(feats)   # feats = single_stock_features(df)[0],列 = 該股自己的交易日
  規則(保守,寧可多遮一天):
    - 名稱帶視窗數字的:取最大的數字 + 1(報酬、差分都要多一天)。
    - 串接平滑的另外算:MACD = 慢線 + 訊號線;TRIX = 3 × 15 + 1;ADX = 2n;隨機指標 = n + 2s;StochRSI = 14 + 14 + 1;TSI = 25 + 13 + 1。
    - 指數平滑(EMA、Wilder)理論上記憶無限長,慣例以一個視窗當暖機期。
    - 「距上次事件幾天」= 事件旗標自己的暖機 + 上限天數(沒看過事件又還沒滿上限天數時,其實不知道答案)。
    - 同日內為常數的市場層級 / 日曆特徵不遮(它們不是用個股自己的歷史算的)。
"""
import re
import numpy as np
import pandas as pd

_FIXED = {
    "body_range": 1, "upper_shadow": 1, "lower_shadow": 1, "close_pos": 1, "range_ratio": 1, "gap": 2,
    "ev_vol_x_avg20": 20, "ev_vol_vs_max20": 21, "ev_spike2": 20, "ev_spike3": 20, "ev_spike2_cnt5": 24, "ev_spike2_cnt20": 39,
    "ev_days_since_spike3": 80, "ev_spike_up": 20, "ev_spike_down": 20, "ev_spike_long_red": 20, "ev_spike_long_black": 20,
    "ev_spike_ret": 20, "ev_vol_dryup": 20, "ev_vol_dryup_cnt5": 24, "ev_gap_up_spike": 20, "ev_gap_down_spike": 20, "ev_range_x_atr": 20,
    "ev_limit_up": 2, "ev_limit_down": 2, "ev_limit_up_lock": 2, "ev_limit_down_lock": 2, "ev_touch_limit_up": 2,
    "ev_limit_up_cnt20": 21, "ev_limit_down_cnt20": 21, "ev_days_since_limit_up": 22, "ev_days_since_limit_down": 22,
    "ev_breakout_vol_60": 61, "ev_breakdown_vol_60": 61,
    "ev_streak": 2, "ev_up_streak_ge3": 4, "ev_down_streak_ge3": 4, "ev_bigmove5": 2, "ev_bigmove5_cnt20": 21,
    "psar_dist": 20, "trix_15": 46, "stochrsi_14": 29, "tsi_25_13": 39, "keltner_pos_20_10": 21, "force_13": 21,
}
_NO_MASK = re.compile(r"^(mkt_roc_\d+|dow_\d+)$")


def required_rows(name: str) -> int:
    if _NO_MASK.match(name):
        return 0
    if name in _FIXED:
        return _FIXED[name]
    nums = [int(x) for x in re.findall(r"\d+", name)]
    if name.startswith(("macd_", "ppo_")):
        return nums[1] + (nums[2] if len(nums) > 2 else 0)
    if name.startswith("adx_"):
        return 2 * nums[0]
    if name.startswith(("stoch_k_", "stoch_d_")):
        return nums[0] + 2 * nums[1]
    if name.startswith("cumret_ex"):
        return nums[-1] + 1
    if not nums:
        raise KeyError(f"沒有定義回看期: {name}")
    return max(nums) + 1


def apply_lookback_mask(feats: pd.DataFrame) -> pd.DataFrame:
    """feats 的列 = 單一股票依時間排序的交易日。第 i 列(0 起算)的歷史列數 = i + 1。"""
    out = feats.copy()
    n = len(out)
    for col in out.columns:
        k = required_rows(col) - 1
        if k > 0:
            out.iloc[:min(k, n), out.columns.get_loc(col)] = np.nan
    return out
