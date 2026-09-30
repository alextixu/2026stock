"""模型層:載入該季三顆 XGBRegressor,輸出當日池內排名分數。

模型是 XGBoost 原生 JSON,用 XGBRegressor().load_model() 讀。
訓練目標 = r_oc_h1(T+1 開盤買、T+1 收盤賣的報酬)在**當日池內的百分位**,
所以輸出是「相對分數」:同一天互相比較才有意義,不要設 score > 0.7 這種絕對門檻。
三顆 seed 的預測取算術平均,與訓練時的做法一致。
"""
import os

import numpy as np
import pandas as pd

from .config import FEATURE_CSV, MODEL_DIR, QUARTER, SEEDS

F38 = pd.read_csv(FEATURE_CSV).feature.tolist()


def resolve_quarter(as_of, quarter=None):
    day = pd.Timestamp(as_of)
    expected = f"{day.year}Q{day.quarter}"
    chosen = quarter or QUARTER or expected
    if chosen != expected:
        raise ValueError(f"模型季度 {chosen} 不符合資料日 {day.date()}，應為 {expected}")
    return chosen


class Model:
    def __init__(self, as_of, quarter=None, seeds=SEEDS):
        self.as_of = pd.Timestamp(as_of)
        quarter = resolve_quarter(self.as_of, quarter)
        files = [os.path.join(MODEL_DIR, f"{quarter}_regq_seed{s}.json") for s in seeds]
        missing = [fp for fp in files if not os.path.isfile(fp)]
        if not seeds or missing:
            raise FileNotFoundError(f"模型不齊全，需提供 adjpx/{quarter} 的全部 seed 模型: {missing}")
        from xgboost import XGBRegressor
        self.quarter, self.model_dir, self.seeds = quarter, MODEL_DIR, seeds
        self.source = "adjpx"
        self.models = []
        for fp in files:
            m = XGBRegressor()
            m.load_model(fp)
            booster = m.get_booster()
            if booster.num_features() != len(F38) or (booster.feature_names and booster.feature_names != F38):
                raise ValueError(f"模型輸入特徵不符: {fp}")
            self.models.append(m)

    @property
    def version(self):
        return f"{self.source}/{self.quarter}/regq/seed{'+'.join(map(str, self.seeds))}"

    def predict(self, X: pd.DataFrame) -> pd.Series:
        """X: DataFrame(index 任意, 欄位必須是 F38 且順序相同) → 分數 Series。"""
        if list(X.columns) != F38:
            bad = [c for c in F38 if c not in X.columns]
            raise ValueError(f"特徵欄位不符。缺 {bad[:5]}…;或順序與 feature_shortlist_v2.csv 不同")
        A = X.to_numpy(dtype=np.float32)          # NaN 直接餵給 XGBoost,訓練時也是這樣
        if X.empty or np.isinf(A).any() or np.isnan(A).all(axis=0).any():
            raise ValueError("模型輸入為空、含無限值或整欄缺失")
        p = np.mean([m.predict(A) for m in self.models], axis=0)
        if not np.isfinite(p).all():
            raise ValueError("模型輸出含無效分數")
        return pd.Series(p, index=X.index, name="score")

    def rank(self, X: pd.DataFrame) -> pd.DataFrame:
        """回傳依分數由高到低排序的 DataFrame[score, rank]。"""
        s = self.predict(X).sort_values(ascending=False)
        return pd.DataFrame({"score": s, "rank": np.arange(1, len(s) + 1)}, index=s.index)
