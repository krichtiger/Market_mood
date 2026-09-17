"""
1단계: 고점 대비 -30% 이상 하락한 국내 종목 스크리너 (프로토타입)

전제:
- pykrx 설치 필요: pip install pykrx
- KRX_ID, KRX_PW 환경변수 설정 필요 (2025.12.27부터 로그인 필수)
- KOSPI 시가총액 상위 200위 + KOSDAQ 시가총액 상위 200위 종목을 대상으로,
  최근 20영업일(약 1개월) 중 최고가 대비 현재 종가가 -30% 이상 하락한 종목을 찾는다.
- 그중에서 최근 5영업일(약 1주일)간 변동성(최고-최저 변동폭)이 5% 이내로
  작은 보합 상태인 종목을 추가로 골라낸다.

주의:
- 시가총액 상위 종목만 순회하므로 예전(전종목) 버전보다 훨씬 빠르게 끝남
- KRX 서버 부하를 줄이기 위해 종목 사이에 짧은 딜레이를 둠
- 이 스크립트는 로컬 또는 Claude Code(클라우드)에서 실행해야 함
  (이 채팅 환경은 인터넷 접속이 막혀 있어 여기서는 실행 결과를 확인할 수 없음)
"""

import json
import time
import datetime
from pykrx import stock

# ----- 설정값 -----
LOOKBACK_DAYS = 20          # 최근 1개월(영업일 기준)로 볼 기간
DROP_THRESHOLD = 0.30       # 고점 대비 하락률 기준 (30%)
REQUEST_DELAY_SEC = 0.3     # 종목간 호출 딜레이 (서버 부하/레이트리밋 방지)
MARKETS = ["KOSPI", "KOSDAQ"]
TOP_N_PER_MARKET = 200      # 시장별 시가총액 상위 몇 위까지 대상으로 할지

FLAT_LOOKBACK_DAYS = 5          # 보합 여부를 판단할 최근 기간 (약 1주일, 영업일 기준)
FLAT_VOLATILITY_THRESHOLD = 0.05   # 기간 내 종가 최고/최저 변동폭이 이 이내면 보합


def get_date_range(lookback_days: int):
    """오늘 기준 최근 lookback_days 영업일에 해당하는 (시작일, 종료일) 문자열 반환"""
    today = datetime.date.today()
    # 여유있게 달력일 기준으로 lookback_days*2 정도를 잡아서 영업일 부족 문제 방지
    start = today - datetime.timedelta(days=lookback_days * 2)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def get_top_market_cap_tickers(market: str, top_n: int):
    """market(KOSPI/KOSDAQ)의 시가총액 상위 top_n개 종목 코드/이름을 가져온다"""
    today = datetime.date.today().strftime("%Y%m%d")
    cap_df = stock.get_market_cap_by_ticker(today, market=market, alternative=True)
    top = cap_df.sort_values("시가총액", ascending=False).head(top_n)

    tickers = []
    for code in top.index:
        name = stock.get_market_ticker_name(code)
        tickers.append({"code": code, "name": name, "market": market})
    return tickers


def get_all_tickers():
    """KOSPI + KOSDAQ 시가총액 상위 종목(시장별 TOP_N_PER_MARKET개)의 코드와 이름을 가져온다"""
    tickers = []
    for market in MARKETS:
        tickers.extend(get_top_market_cap_tickers(market, TOP_N_PER_MARKET))
    return tickers


def check_recent_flat(df, lookback_days: int, volatility_threshold: float):
    """
    df의 마지막 lookback_days개 종가를 기준으로 최근 보합 여부를 판단.
    (기간 내 최고/최저 종가 변동폭이 기준 이내면 보합)
    """
    recent = df.tail(lookback_days)
    if len(recent) < 2:
        return False, None

    ref_close = recent["종가"].iloc[0]
    if ref_close <= 0:
        return False, None

    volatility_pct = (recent["종가"].max() - recent["종가"].min()) / ref_close
    is_flat = bool(volatility_pct <= volatility_threshold)

    return is_flat, float(round(volatility_pct * 100, 1))


def check_drop_from_high(code: str, start: str, end: str, threshold: float):
    """
    특정 종목에 대해 최근 기간 내 최고가 대비 현재가 하락률을 계산.
    threshold 이상 하락했으면 결과 dict, 아니면 None 반환.
    """
    try:
        df = stock.get_market_ohlcv(start, end, code)
    except Exception as e:
        print(f"[SKIP] {code} 데이터 조회 실패: {e}")
        return None

    if df is None or df.empty:
        return None

    recent_high = df["고가"].max()
    current_close = df["종가"].iloc[-1]

    if recent_high <= 0:
        return None

    drop_ratio = (recent_high - current_close) / recent_high

    if drop_ratio >= threshold:
        is_flat, recent_volatility_pct = check_recent_flat(
            df, FLAT_LOOKBACK_DAYS, FLAT_VOLATILITY_THRESHOLD
        )
        return {
            "code": code,
            "recent_high": int(recent_high),
            "current_close": int(current_close),
            "drop_ratio": float(round(drop_ratio * 100, 1)),
            "is_recent_flat": is_flat,
            "recent_volatility_pct": recent_volatility_pct,
        }
    return None


def run_screener():
    start, end = get_date_range(LOOKBACK_DAYS)
    print(f"조회 기간: {start} ~ {end}")

    tickers = get_all_tickers()
    print(f"전체 대상 종목 수: {len(tickers)} (시장별 시가총액 상위 {TOP_N_PER_MARKET}개)")

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


def save_results_json(results, flat_results, path="screener_output.json"):
    """다음 단계(재무분석 등)가 읽을 결과 파일 생성"""
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
