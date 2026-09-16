"""
1단계: 고점 대비 -30% 이상 하락한 국내 종목 스크리너 (프로토타입)

전제:
- pykrx 설치 필요: pip install pykrx
- KRX_ID, KRX_PW 환경변수 설정 필요 (2025.12.27부터 로그인 필수)
- KOSPI + KOSDAQ 전종목을 대상으로 최근 20영업일(약 1개월) 중 최고가 대비
  현재 종가가 -30% 이상 하락한 종목을 찾는다.
- 그중에서 최근 5영업일(약 1주일)간 종가 변화율/변동폭이 작아 보합 상태인
  종목을 추가로 골라낸다.

주의:
- 전종목(2천개 이상) 순회라 API 호출이 많음 -> 실행 시간이 김 (수 분 이상)
- Yahoo/KRX 서버 부하를 줄이기 위해 종목 사이에 짧은 딜레이를 둠
- 이 스크립트는 로컬 또는 Claude Code(클라우드)에서 실행해야 함
  (이 채팅 환경은 인터넷 접속이 막혀 있어 여기서는 실행 결과를 확인할 수 없음)
"""

import time
import datetime
from pykrx import stock

# ----- 설정값 -----
LOOKBACK_DAYS = 20          # 최근 1개월(영업일 기준)로 볼 기간
DROP_THRESHOLD = 0.30       # 고점 대비 하락률 기준 (30%)
REQUEST_DELAY_SEC = 0.3     # 종목간 호출 딜레이 (서버 부하/레이트리밋 방지)
MARKETS = ["KOSPI", "KOSDAQ"]

FLAT_LOOKBACK_DAYS = 5      # 보합 여부를 판단할 최근 기간 (약 1주일, 영업일 기준)
FLAT_CHANGE_THRESHOLD = 0.03   # 기간 시작~종료 종가 변화율이 이 이내면 보합
FLAT_RANGE_THRESHOLD = 0.05    # 기간 내 종가 최고/최저 변동폭이 이 이내면 보합


def get_date_range(lookback_days: int):
    """오늘 기준 최근 lookback_days 영업일에 해당하는 (시작일, 종료일) 문자열 반환"""
    today = datetime.date.today()
    # 여유있게 달력일 기준으로 lookback_days*2 정도를 잡아서 영업일 부족 문제 방지
    start = today - datetime.timedelta(days=lookback_days * 2)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def get_all_tickers():
    """KOSPI + KOSDAQ 전종목 코드와 이름을 가져온다"""
    tickers = []
    for market in MARKETS:
        codes = stock.get_market_ticker_list(market=market)
        for code in codes:
            name = stock.get_market_ticker_name(code)
            tickers.append({"code": code, "name": name, "market": market})
    return tickers


def check_recent_flat(df, lookback_days: int, change_threshold: float, range_threshold: float):
    """
    df의 마지막 lookback_days개 종가를 기준으로 최근 보합 여부를 판단.
    (시작~종료 종가 변화율, 기간 내 최고/최저 변동폭이 모두 기준 이내면 보합)
    """
    recent = df.tail(lookback_days)
    if len(recent) < 2:
        return False, None, None

    start_close = recent["종가"].iloc[0]
    end_close = recent["종가"].iloc[-1]
    if start_close <= 0:
        return False, None, None

    change_pct = (end_close - start_close) / start_close
    range_pct = (recent["종가"].max() - recent["종가"].min()) / start_close
    is_flat = abs(change_pct) <= change_threshold and range_pct <= range_threshold

    return is_flat, round(change_pct * 100, 1), round(range_pct * 100, 1)


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
        is_flat, recent_change_pct, recent_range_pct = check_recent_flat(
            df, FLAT_LOOKBACK_DAYS, FLAT_CHANGE_THRESHOLD, FLAT_RANGE_THRESHOLD
        )
        return {
            "code": code,
            "recent_high": int(recent_high),
            "current_close": int(current_close),
            "drop_ratio": round(drop_ratio * 100, 1),
            "is_recent_flat": is_flat,
            "recent_change_pct": recent_change_pct,
            "recent_range_pct": recent_range_pct,
        }
    return None


def run_screener():
    start, end = get_date_range(LOOKBACK_DAYS)
    print(f"조회 기간: {start} ~ {end}")

    tickers = get_all_tickers()
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


if __name__ == "__main__":
    results, flat_results = run_screener()

    print("\n--- 고점 대비 -30% 이상 하락 종목 전체 ---")
    for r in results:
        print(r)

    print("\n--- 그중 최근 1주일가량 보합인 종목 ---")
    for r in flat_results:
        print(r)
