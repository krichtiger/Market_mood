"""
1단계(미국): 고점 대비 -30% 이상 하락한 미국 종목 스크리너 (프로토타입)

국내용 screener_prototype.py와 동일한 로직/함수 구조를 미국 종목에 적용한다.

전제:
- yfinance 설치 필요: pip install yfinance
- S&P500 종목 중 시가총액 상위 100개를 대상으로, 최근 20영업일(약 1개월) 중
  최고가 대비 현재 종가가 -30% 이상 하락한 종목을 찾는다.
- 그중에서 최근 5영업일(약 1주일)간 변동성(최고-최저 변동폭)이 5% 이내로
  작은 보합 상태인 종목을 추가로 골라낸다.
- 종목 유니버스는 위키피디아의 S&P500 구성종목 표를 읽어온 뒤, 시가총액으로
  정렬해 상위 TOP_N개만 사용한다.
  (다른 유니버스로 확장하려면 UNIVERSE_SOURCES에 소스만 추가하면 됨)

주의:
- 시가총액 조회(전체 S&P500) + 상위 종목 OHLCV 조회, 두 단계로 API를 호출하므로
  시간이 다소 걸림
- Yahoo Finance 서버 부하를 줄이기 위해 종목 사이에 짧은 딜레이를 둠
- 이 스크립트는 로컬 또는 Claude Code(클라우드)에서 실행해야 함
  (이 채팅 환경은 인터넷 접속이 막혀 있어 여기서는 실행 결과를 확인할 수 없음)
"""

import io
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
TOP_N = 100                 # 유니버스 소스들 중 시가총액 상위 몇 위까지 대상으로 할지

FLAT_LOOKBACK_DAYS = 5          # 보합 여부를 판단할 최근 기간 (약 1주일, 영업일 기준)
FLAT_VOLATILITY_THRESHOLD = 0.05   # 기간 내 종가 최고/최저 변동폭이 이 이내면 보합

# 유니버스 소스: (위키피디아 URL, 심볼 컬럼 후보들, 종목명 컬럼 후보들, market 태그)
UNIVERSE_SOURCES = [
    (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ["Symbol"],
        ["Security", "Company"],
        "S&P500",
    ),
]


def get_date_range(lookback_days: int):
    """오늘 기준 최근 lookback_days 영업일에 해당하는 (시작일, 종료일) 문자열 반환 (yfinance용 YYYY-MM-DD)"""
    today = datetime.date.today()
    # 여유있게 달력일 기준으로 lookback_days*2 정도를 잡아서 영업일 부족 문제 방지
    start = today - datetime.timedelta(days=lookback_days * 2)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _match_column(columns, candidates):
    """
    표의 컬럼 목록에서 candidates 중 하나와 (대소문자 무시 + 부분 일치) 매칭되는
    실제 컬럼 키를 반환. MultiIndex 컬럼(여러 헤더 행)도 각 레벨을 이어붙여 비교.
    매칭 없으면 None.
    """
    for col in columns:
        col_text = "|".join(str(x) for x in col) if isinstance(col, tuple) else str(col)
        for cand in candidates:
            if cand.lower() in col_text.lower():
                return col
    return None


def _find_table_with_column(tables, column_candidates, min_rows=20):
    """
    읽어온 여러 표 중 원하는 컬럼이 있는 표를 반환. 지수 구성종목 표는 보통
    수십~수백 행이므로, 같은 컬럼명을 가진 작은(관련 없을 가능성이 큰) 표보다
    min_rows 이상인 표를 우선한다.
    """
    for require_min_rows in (True, False):
        for table in tables:
            if require_min_rows and len(table) < min_rows:
                continue
            matched = _match_column(table.columns, column_candidates)
            if matched is not None:
                return table, matched

    print(f"[DEBUG] 컬럼 {column_candidates}을 가진 표를 찾지 못함. 발견된 표들의 컬럼 목록:")
    for i, table in enumerate(tables):
        print(f"  table[{i}] rows={len(table)} columns: {list(table.columns)}")
    raise ValueError(f"컬럼 {column_candidates}을 가진 표를 찾지 못함")


def get_raw_universe_tickers():
    """
    UNIVERSE_SOURCES에 등록된 소스들의 종목 코드/이름을 위키피디아에서 가져온다
    (중복 종목은 market을 합쳐서 표시). 시가총액 필터링 전 원본 목록.

    소스 중 하나(예: 위키피디아 문서 구조 변경으로 표를 못 찾는 경우)가 실패해도
    전체 스크리너가 죽지 않도록, 소스별로 개별 처리하고 실패한 소스는 건너뛴다.
    """
    merged = {}
    for url, symbol_cols, name_cols, market in UNIVERSE_SOURCES:
        try:
            resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
            resp.raise_for_status()
            tables = pd.read_html(io.StringIO(resp.text))
            table, symbol_col = _find_table_with_column(tables, symbol_cols)
            name_col = _match_column(table.columns, name_cols)
        except Exception as e:
            print(f"[WARN] {market} 유니버스 소스({url}) 조회 실패, 이 소스는 건너뜀: {e}")
            continue

        for _, row in table.iterrows():
            symbol = str(row[symbol_col]).strip().replace(".", "-")
            name = str(row[name_col]).strip() if name_col else symbol
            if symbol not in merged:
                merged[symbol] = {"code": symbol, "name": name, "markets": set()}
            merged[symbol]["markets"].add(market)

    if not merged:
        raise RuntimeError("모든 유니버스 소스 조회에 실패해서 대상 종목이 없음")

    return [
        {"code": t["code"], "name": t["name"], "market": "+".join(sorted(t["markets"]))}
        for t in merged.values()
    ]


def get_universe_tickers():
    """원본 유니버스에서 시가총액 상위 TOP_N개 종목만 추려서 반환"""
    tickers = get_raw_universe_tickers()
    print(f"원본 유니버스 종목 수: {len(tickers)} -> 시가총액 조회 후 상위 {TOP_N}개로 축소")

    ranked = []
    for t in tickers:
        try:
            market_cap = yf.Ticker(t["code"]).info.get("marketCap")
        except Exception as e:
            print(f"[SKIP] {t['code']} 시가총액 조회 실패: {e}")
            market_cap = None

        if market_cap:
            ranked.append((market_cap, t))

        time.sleep(REQUEST_DELAY_SEC)

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in ranked[:TOP_N]]


def check_recent_flat(df, lookback_days: int, volatility_threshold: float):
    """
    df의 마지막 lookback_days개 종가를 기준으로 최근 보합 여부를 판단.
    (기간 내 최고/최저 종가 변동폭이 기준 이내면 보합)
    """
    recent = df.tail(lookback_days)
    if len(recent) < 2:
        return False, None

    ref_close = recent["Close"].iloc[0]
    if ref_close <= 0:
        return False, None

    volatility_pct = (recent["Close"].max() - recent["Close"].min()) / ref_close
    is_flat = bool(volatility_pct <= volatility_threshold)

    return is_flat, float(round(volatility_pct * 100, 1))


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
        is_flat, recent_volatility_pct = check_recent_flat(
            df, FLAT_LOOKBACK_DAYS, FLAT_VOLATILITY_THRESHOLD
        )
        return {
            "code": code,
            "recent_high": float(round(recent_high, 2)),
            "current_close": float(round(current_close, 2)),
            "drop_ratio": float(round(drop_ratio * 100, 1)),
            "is_recent_flat": is_flat,
            "recent_volatility_pct": recent_volatility_pct,
        }
    return None


def run_screener():
    start, end = get_date_range(LOOKBACK_DAYS)
    print(f"조회 기간: {start} ~ {end}")

    tickers = get_universe_tickers()
    print(f"전체 대상 종목 수: {len(tickers)} (시가총액 상위 {TOP_N}개)")

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
    print(f"그중 최근 {FLAT_LOOKBACK_DAYS}영업일간 변동성 {int(FLAT_VOLATILITY_THRESHOLD*100)}% 이내(보합)인 종목: {len(flat_results)}개")

    return results, flat_results


def save_results_json(results, flat_results, path="screener_us_output.json"):
    """다음 단계(재무분석 등)가 읽을 결과 파일 생성 (screener_prototype.py의 save_results_json과 동일 패턴)"""
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
