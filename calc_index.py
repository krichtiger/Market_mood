"""
공포탐욕지수 계산 스크립트
------------------------------------------------------------
이 파일은 이 대화창(샌드박스)에는 인터넷이 연결되어 있지 않아
직접 실행해서 결과를 보여드릴 수는 없습니다.
Cursor + 본인 컴퓨터(또는 GitHub Actions)에서 실행해주세요.

설치:
    pip install pykrx yfinance pandas

실행:
    python calc_index.py
    -> 같은 폴더에 output.json 생성됨 (웹페이지 sampleData와 같은 구조)
------------------------------------------------------------

방법론 (PRD 5번 참고, 최초 버전 — 지표 2개로 단순화):
  1) 모멘텀   : 현재가가 25일 이동평균 대비 얼마나 높은가
  2) 52주 위치 : 최근 1년 최고~최저 구간에서 현재가의 위치

두 점수를 0~100으로 정규화한 뒤 평균 -> 최종 지수
(추후 변동성 지표(VIX/VKOSPI), 시장 폭 등을 추가해 CNN처럼 정교화 가능)
"""

import json
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock as krx
import yfinance as yf


# ------------------------------------------------------------------
# 공통 유틸
# ------------------------------------------------------------------

def score_momentum(close: pd.Series, window: int = 25) -> float:
    """현재가 vs N일 이동평균. 이평 대비 +-5% 범위를 0~100으로 정규화."""
    ma = close.rolling(window).mean().iloc[-1]
    now = close.iloc[-1]
    pct = (now - ma) / ma  # 예: 0.03 = 이평보다 3% 위
    # -5% ~ +5% 범위를 0~100으로 매핑, 범위 밖은 clamp
    score = (pct + 0.05) / 0.10 * 100
    return max(0, min(100, score))


def score_52w_position(close: pd.Series) -> float:
    """52주(약 252거래일) 최고~최저 구간에서 현재가 위치를 0~100으로."""
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


# ------------------------------------------------------------------
# 국내 (pykrx)
# ------------------------------------------------------------------

def get_kr_close_series(index_ticker: str, days: int = 400) -> pd.Series:
    """
    index_ticker 예시:
      "1001" = 코스피
      "2001" = 코스닥
      "1157" = 코스피200 정보기술 (반도체 근사)
      "1154" = 코스피200 에너지/화학 (2차전지 근사, 정확히는 확인 필요)
      "1156" = 코스피200 금융
    ※ 정확한 업종 코드는 pykrx.stock.get_index_ticker_list()로 직접 확인해서
      원하는 업종에 맞게 교체해주세요. (반도체/2차전지/바이오처럼 세분화된
      테마는 공식 지수가 없을 수 있어, 그 경우 관련 ETF 가격으로 대체하는
      방법도 있습니다 -> get_kr_etf_close_series() 참고)
    """
    end = datetime.today()
    start = end - timedelta(days=days)
    df = krx.get_index_ohlcv_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), index_ticker
    )
    return df["종가"]


def get_kr_etf_close_series(etf_ticker: str, days: int = 400) -> pd.Series:
    """세분화된 테마(반도체/2차전지/바이오 등)는 대표 ETF 가격으로 근사.
    예: KODEX 반도체, TIGER 2차전지테마, TIGER 헬스케어 등
    정확한 티커는 pykrx.stock.get_etf_ticker_list()로 확인 후 교체하세요."""
    end = datetime.today()
    start = end - timedelta(days=days)
    df = krx.get_etf_ohlcv_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), etf_ticker
    )
    return df["종가"]


# ------------------------------------------------------------------
# 미국 (yfinance)
# ------------------------------------------------------------------

def get_us_close_series(ticker: str, days: int = 400) -> pd.Series:
    """
    ticker 예시:
      "^GSPC" = S&P500
      "XLK"   = 빅테크/기술 섹터 ETF
      "XLE"   = 에너지 섹터 ETF
      "XLV"   = 헬스케어 섹터 ETF
      "XLF"   = 금융 섹터 ETF
    """
    df = yf.download(ticker, period=f"{days}d", progress=False)
    return df["Close"].squeeze()


# ------------------------------------------------------------------
# 메인: sampleData와 동일한 구조로 output.json 생성
# ------------------------------------------------------------------

def main():
    result = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M 기준"),
        "markets": [
            {"key": "kospi", "name": "코스피", "sub": "국내 시장 전체",
             "value": combine(get_kr_close_series("1001"))},
            {"key": "us", "name": "S&P500", "sub": "미국 시장 전체",
             "value": combine(get_us_close_series("^GSPC"))},
        ],
        "krSectors": [
            # 아래 코드/티커는 예시입니다. 실제 값으로 검증 후 교체하세요.
            {"name": "정보기술", "value": combine(get_kr_close_series("1157"))},
            {"name": "금융",     "value": combine(get_kr_close_series("1156"))},
            # 반도체/2차전지/바이오처럼 세분화된 업종은 ETF로 대체 추천:
            # {"name": "반도체", "value": combine(get_kr_etf_close_series("091160"))},  # KODEX 반도체 예시
        ],
        "usSectors": [
            {"name": "빅테크",   "value": combine(get_us_close_series("XLK"))},
            {"name": "에너지",   "value": combine(get_us_close_series("XLE"))},
            {"name": "헬스케어", "value": combine(get_us_close_series("XLV"))},
            {"name": "금융",     "value": combine(get_us_close_series("XLF"))},
        ],
    }

    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("output.json 생성 완료")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
