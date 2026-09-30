"""Read the packaged universe without relying on training-project modules."""
import pandas as pd

from .config import POOL_CSV


def pool_tickers(path=POOL_CSV):
    frame = pd.read_csv(path, dtype={"code": str})
    if not {"code", "market"}.issubset(frame.columns) or frame.empty:
        raise ValueError("股票池必須包含 code、market，且不得為空")
    if frame[["code", "market"]].isna().any().any():
        raise ValueError("股票池包含空代號或市場")
    if frame.code.duplicated().any() or not frame.code.str.fullmatch(r"[A-Za-z0-9]+").all():
        raise ValueError("股票池代號重複或格式錯誤")
    suffix = frame.market.map({"上市": "TW", "上櫃": "TWO"})
    if suffix.isna().any():
        raise ValueError("股票池市場僅支援上市、上櫃")
    return (frame.code + "." + suffix).tolist()
