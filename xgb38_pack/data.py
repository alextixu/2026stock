"""features.build_panel 需要的論文 9 指標。

build_panel 會算 p9_* 九欄,但 38 欄清單裡一欄都沒用到,推論時整批丟棄;
這裡保留只是為了讓 build_panel 的介面不變(不改動 features/ 任何一行)。
"""
from .indicators import compute_indicators as paper9_indicators  # noqa: F401

PAPER9_NAMES = ["p9_sma", "p9_wma", "p9_mom", "p9_macd", "p9_willr",
                "p9_cci", "p9_stoch_k", "p9_stoch_d", "p9_rsi"]
