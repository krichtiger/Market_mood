"""
공포탐욕지수 계산 스크립트 (v2 — 재시도/오류내성 추가)
------------------------------------------------------------
pip install pykrx yfinance pandas
python calc_index.py
------------------------------------------------------------
"""
import json
import time
from datetime import datetime, timedelta
import pandas as pd
from pykrx import stock as krx
import yfinance as yf
def with_retry(func, *args, tries=3, delay=3, **kwargs):
    """일시적인 데이터 소스 오류에 대비해 재시도."""
    last_err = None
    for i in range(tries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            print(f"  ...실패 ({i+1}/{tries}): {e}")
            time.sleep(delay)
    print(f"  ...최종 실패, 이 항목은 건너뜁니다: {last_err}")
    return None
def score_momentum(close: pd.Series, window: int = 25) -> float:
    ma = close.rolling(window).mean().iloc[-1]
    now = close.iloc[-1]
    pct = (now - ma) / ma
    score = (pct + 0.05) / 0.10 * 100
    return max(0, min(100, score))
def score_52w_position(close: pd.Series) -> float:
    recent = close.tail(252)
    lo, hi = recent.min(), recent.max()
    now = close.iloc[-1]
    if hi == lo:
        return 50.0
    score = (now - lo) / (hi - lo) * 100
    return max(0, min(100, score))
def combine(close: pd.Series) -> int:
    m = score_momentum(close)
    p = score_52w_position(close)
    return round((m + p) / 2)
def _fetch_kr(index_ticker: str, days: int = 400) -> pd.Series:
    end = datetime.today()
    start = end - timedelta(days=days)
    df = krx.get_index_ohlcv_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), index_ticker
    )
    if df is None or df.empty:
        raise ValueError("빈 데이터프레임 반환됨")
    return df["종가"]
def get_kr_score(index_ticker: str):
    close = with_retry(_fetch_kr, index_ticker)
    if close is None:
        return None
    return combine(close)
def _fetch_us(ticker: str, days: int = 400) -> pd.Series:
    df = yf.download(ticker, period=f"{days}d", progress=False)
    if df is None or df.empty:
        raise ValueError("빈 데이터프레임 반환됨")
    return df["Close"].squeeze()
def get_us_score(ticker: str):
    close = with_retry(_fetch_us, ticker)
    if close is None:
        return None
    return combine(close)
def main():
    print("코스피 계산 중...")
    kospi = get_kr_score("1001")
    print("S&P500 계산 중...")
    sp500 = get_us_score("^GSPC")
    print("국내 업종(정보기술/금융) 계산 중...")
    kr_it = get_kr_score("1157")
    kr_fin = get_kr_score("1156")
    print("미국 섹터 계산 중...")
    us_tech = get_us_score("XLK")
    us_energy = get_us_score("XLE")
    us_health = get_us_score("XLV")
    us_fin = get_us_score("XLF")
    result = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M 기준"),
        "markets": [
            {"key": "kospi", "name": "코스피", "sub": "국내 시장 전체", "value": kospi},
            {"key": "us", "name": "S&P500", "sub": "미국 시장 전체", "value": sp500},
        ],
        "krSectors": [
            {"name": "정보기술", "value": kr_it},
            {"name": "금융", "value": kr_fin},
        ],
        "usSectors": [
            {"name": "빅테크", "value": us_tech},
            {"name": "에너지", "value": us_energy},
            {"name": "헬스케어", "value": us_health},
            {"name": "금융", "value": us_fin},
        ],
    }

    # 실패한(None) 항목은 결과에서 제외 (완전히 죽지 않고 나머지는 발송)
    result["markets"] = [m for m in result["markets"] if m["value"] is not None]
    result["krSectors"] = [s for s in result["krSectors"] if s["value"] is not None]
    result["usSectors"] = [s for s in result["usSectors"] if s["value"] is not None]

    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("output.json 생성 완료")
    print(json.dumps(result, ensure_ascii=False, indent=2))
if __name__ == "__main__":
    main()
