"""
1단계(미국): 고점 대비 -30% 이상 하락한 미국 종목 스크리너 (프로토타입)

국내용 screener_prototype.py와 동일한 로직/함수 구조를 미국 종목에 적용한다.

전제:
- yfinance 설치 필요: pip install yfinance
- S&P500 + 나스닥100 종목을 대상으로 최근 20영업일(약 1개월) 중 최고가 대비
  현재 종가가 -30% 이상 하락한 종목을 찾는다.
- 그중에서 최근 5영업일(약 1주일)간 종가 변화율/변동폭이 작아 보합 상태인
  종목을 추가로 골라낸다.
- 종목 유니버스는 위키피디아의 S&P500 / 나스닥100 구성종목 표를 읽어온다.
  (다른 유니버스로 확장하려면 get_universe_tickers()에 소스만 추가하면 됨)

주의:
- 유니버스 크기(약 500~600개, 중복 제외) 순회라 API 호출이 많음 -> 실행 시간이 김
- Yahoo Finance 서버 부하를 줄이기 위해 종목 사이에 짧은 딜레이를 둠
- 이 스크립트는 로컬 또는 Claude Code(클라우드)에서 실행해야 함
  (이 채팅 환경은 인터넷 접속이 막혀 있어 여기서는 실행 결과를 확인할 수 없음)
"""

import json
import time
import datetime

import pandas as pd
import requests
import yfinance as yf

# 위키피디아는 브라우저 User-Agent가 없는 요청(기본 urllib/pandas)을 403으로 차단하므로
# requests로 직접 받아온 뒤 pd.read_html에 HTML 문자열을 넘긴다.
WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MarketMoodScreener/1.0)"}

# ----- 설정값 -----
LOOKBACK_DAYS = 20          # 최근 1개월(영업일 기준)로 볼 기간
DROP_THRESHOLD = 0.30       # 고점 대비 하락률 기준 (30%)
REQUEST_DELAY_SEC = 0.3     # 종목간 호출 딜레이 (서버 부하/레이트리밋 방지)

FLAT_LOOKBACK_DAYS = 5      # 보합 여부를 판단할 최근 기간 (약 1주일, 영업일 기준)
FLAT_CHANGE_THRESHOLD = 0.03   # 기간 시작~종료 종가 변화율이 이 이내면 보합
FLAT_RANGE_THRESHOLD = 0.05    # 기간 내 종가 최고/최저 변동폭이 이 이내면 보합

# 유니버스 소스: (위키피디아 URL, 심볼 컬럼 후보들, 종목명 컬럼 후보들, market 태그)
UNIVERSE_SOURCES = [
    (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ["Symbol"],
        ["Security", "Company"],
        "S&P500",
    ),
    (
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        ["Ticker", "Symbol"],
        ["Company", "Name"],
        "NASDAQ100",
    ),
]


def get_date_range(lookback_days: int):
    """오늘 기준 최근 lookback_days 영업일에 해당하는 (시작일, 종료일) 문자열 반환 (yfinance용 YYYY-MM-DD)"""
    today = datetime.date.today()
    # 여유있게 달력일 기준으로 lookback_days*2 정도를 잡아서 영업일 부족 문제 방지
    start = today - datetime.timedelta(days=lookback_days * 2)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _find_table_with_column(tables, column_candidates):
    """읽어온 여러 표 중 원하는 컬럼이 있는 첫 표를 반환"""
    for table in tables:
        for col in column_candidates:
            if col in table.columns:
                return table, col
    raise ValueError(f"컬럼 {column_candidates}을 가진 표를 찾지 못함")


def get_universe_tickers():
    """S&P500 + 나스닥100 종목 코드와 이름을 위키피디아에서 가져온다 (중복 종목은 market을 합쳐서 표시)"""
    merged = {}
    for url, symbol_cols, name_cols, market in UNIVERSE_SOURCES:
        resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        table, symbol_col = _find_table_with_column(tables, symbol_cols)
        name_col = next((c for c in name_cols if c in table.columns), None)

        for _, row in table.iterrows():
            symbol = str(row[symbol_col]).strip().replace(".", "-")
            name = str(row[name_col]).strip() if name_col else symbol
            if symbol not in merged:
                merged[symbol] = {"code": symbol, "name": name, "markets": set()}
            merged[symbol]["markets"].add(market)

    tickers = [
        {"code": t["code"], "name": t["name"], "market": "+".join(sorted(t["markets"]))}
        for t in merged.values()
    ]
    return tickers


def check_recent_flat(df, lookback_days: int, change_threshold: float, range_threshold: float):
    """
    df의 마지막 lookback_days개 종가를 기준으로 최근 보합 여부를 판단.
    (시작~종료 종가 변화율, 기간 내 최고/최저 변동폭이 모두 기준 이내면 보합)
    """
    recent = df.tail(lookback_days)
    if len(recent) < 2:
        return False, None, None

    start_close = recent["Close"].iloc[0]
    end_close = recent["Close"].iloc[-1]
    if start_close <= 0:
        return False, None, None

    change_pct = (end_close - start_close) / start_close
    range_pct = (recent["Close"].max() - recent["Close"].min()) / start_close
    is_flat = bool(abs(change_pct) <= change_threshold and range_pct <= range_threshold)

    return is_flat, float(round(change_pct * 100, 1)), float(round(range_pct * 100, 1))


def check_drop_from_high(code: str, start: str, end: str, threshold: float):
    """
    특정 종목에 대해 최근 기간 내 최고가 대비 현재가 하락률을 계산.
    threshold 이상 하락했으면 결과 dict, 아니면 None 반환.
    """
    try:
        df = yf.Ticker(code).history(start=start, end=end, auto_adjust=True)
    except Exception as e:
        print(f"[SKIP] {code} 데이터 조회 실패: {e}")
        return None

    if df is None or df.empty:
        return None

    recent_high = df["High"].max()
    current_close = df["Close"].iloc[-1]

    if recent_high <= 0:
        return None

    drop_ratio = (recent_high - current_close) / recent_high

    if drop_ratio >= threshold:
        is_flat, recent_change_pct, recent_range_pct = check_recent_flat(
            df, FLAT_LOOKBACK_DAYS, FLAT_CHANGE_THRESHOLD, FLAT_RANGE_THRESHOLD
        )
        return {
            "code": code,
            "recent_high": float(round(recent_high, 2)),
            "current_close": float(round(current_close, 2)),
            "drop_ratio": float(round(drop_ratio * 100, 1)),
            "is_recent_flat": is_flat,
            "recent_change_pct": recent_change_pct,
            "recent_range_pct": recent_range_pct,
        }
    return None


def run_screener():
    start, end = get_date_range(LOOKBACK_DAYS)
    print(f"조회 기간: {start} ~ {end}")

    tickers = get_universe_tickers()
    print(f"전체 대상 종목 수: {len(tickers)}")

    results = []
    for i, t in enumerate(tickers):
        res = check_drop_from_high(t["code"], start, end, DROP_THRESHOLD)
        if res:
            res["name"] = t["name"]
            res["market"] = t["market"]
            results.append(res)
            print(f"  [적중] {t['name']}({t['code']}) 고점대비 -{res['drop_ratio']}%")

        if (i + 1) % 100 == 0:
            print(f"진행중... {i + 1}/{len(tickers)}")

        time.sleep(REQUEST_DELAY_SEC)

    # 하락률 큰 순으로 정렬
    results.sort(key=lambda x: x["drop_ratio"], reverse=True)

    print(f"\n총 {len(results)}개 종목이 고점 대비 -{int(DROP_THRESHOLD*100)}% 이상 하락")

    flat_results = [r for r in results if r["is_recent_flat"]]
    print(f"그중 최근 {FLAT_LOOKBACK_DAYS}영업일간 보합인 종목: {len(flat_results)}개")

    return results, flat_results


def save_results_json(results, flat_results, path="screener_us_output.json"):
    """향후 종합 리포트 단계에서 읽을 결과 파일 생성 (screener_prototype.py의 save_results_json과 동일 패턴)"""
    data = {
        "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M 기준"),
        "totalCount": len(results),
        "flatCount": len(flat_results),
        "results": results,
        "flatResults": flat_results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{path} 생성 완료")


if __name__ == "__main__":
    results, flat_results = run_screener()

    print("\n--- 고점 대비 -30% 이상 하락 종목 전체 ---")
    for r in results:
        print(r)

    print("\n--- 그중 최근 1주일가량 보합인 종목 ---")
    for r in flat_results:
        print(r)

    save_results_json(results, flat_results)
