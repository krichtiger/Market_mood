"""
4단계(미국): yfinance 재무 지표로 "기업가치 변동" 여부를 판단한다.

전제:
- 입력: screener_us_output.json의 flatResults (고점 대비 -30% 하락 + 최근 1주 보합 종목)
- yfinance의 .info에서 매출/이익 성장률과 부채비율을 가져와, 실적이 크게
  악화된 종목은 "가치 하락(하락에 정당한 이유 있음)"으로 판단해 제외하고,
  그렇지 않은 종목만 다음 단계(뉴스 분석)로 넘긴다.

판단 기준 (1차 버전, 규칙 기반. 국내판 financial_analysis_kr.py와 같은 사고방식):
- revenueGrowth < REVENUE_DECLINE_THRESHOLD 이거나
  earningsGrowth < EARNINGS_DECLINE_THRESHOLD 이면 "가치 하락"
- debtToEquity(%) > DEBT_RATIO_THRESHOLD 이면 "가치 하락"
- 위 조건에 해당 없으면 "가치 유지"로 보고 통과
"""

import json
import time
import datetime

import yfinance as yf

REQUEST_DELAY_SEC = 0.3

REVENUE_DECLINE_THRESHOLD = -0.20     # 매출 성장률(YoY)이 이보다 낮으면 가치 하락으로 판단
EARNINGS_DECLINE_THRESHOLD = -0.30    # 이익 성장률(YoY)이 이보다 낮으면 가치 하락으로 판단
DEBT_RATIO_THRESHOLD = 200.0          # 부채비율(%)이 이보다 높으면 가치 하락으로 판단


def analyze_financial_value(code: str):
    """종목 하나에 대해 yfinance 재무 지표를 조회하고 가치 변동 여부를 판단"""
    try:
        info = yf.Ticker(code).info
    except Exception as e:
        return {"code": code, "value_maintained": None, "reason": f"재무 정보 조회 실패: {e}"}

    revenue_growth = info.get("revenueGrowth")
    earnings_growth = info.get("earningsGrowth")
    debt_to_equity = info.get("debtToEquity")  # yfinance는 이미 %(예: 45.2) 단위로 제공

    reasons = []
    value_maintained = True

    if revenue_growth is not None and revenue_growth < REVENUE_DECLINE_THRESHOLD:
        value_maintained = False
        reasons.append(f"매출성장률 {round(revenue_growth * 100, 1)}%")

    if earnings_growth is not None and earnings_growth < EARNINGS_DECLINE_THRESHOLD:
        value_maintained = False
        reasons.append(f"이익성장률 {round(earnings_growth * 100, 1)}%")

    if debt_to_equity is not None and debt_to_equity > DEBT_RATIO_THRESHOLD:
        value_maintained = False
        reasons.append(f"부채비율 {round(debt_to_equity, 1)}%")

    return {
        "code": code,
        "revenue_growth_pct": round(revenue_growth * 100, 1) if revenue_growth is not None else None,
        "earnings_growth_pct": round(earnings_growth * 100, 1) if earnings_growth is not None else None,
        "debt_to_equity_pct": round(debt_to_equity, 1) if debt_to_equity is not None else None,
        "trailing_pe": info.get("trailingPE"),
        "price_to_book": info.get("priceToBook"),
        "return_on_equity_pct": round(info["returnOnEquity"] * 100, 1) if info.get("returnOnEquity") is not None else None,
        "value_maintained": value_maintained,
        "reason": "; ".join(reasons) if reasons else "매출/이익 유지, 부채비율 양호",
    }


def run_financial_analysis(candidates):
    """
    candidates: screener_us_output.json의 flatResults 리스트 (각 dict에 code/name/market 포함)
    가치가 유지된(하락 없는) 종목만 리스트로 반환.
    """
    passed = []
    for c in candidates:
        result = {**c, **analyze_financial_value(c["code"])}

        if result["value_maintained"]:
            print(f"  [통과] {c['name']}({c['code']}) - {result['reason']}")
            passed.append(result)
        else:
            print(f"  [제외] {c['name']}({c['code']}) - {result['reason']}")

        time.sleep(REQUEST_DELAY_SEC)

    return passed


def save_results_json(passed, path="financial_analysis_us_output.json"):
    data = {
        "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M 기준"),
        "passedCount": len(passed),
        "passed": passed,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{path} 생성 완료")


if __name__ == "__main__":
    with open("screener_us_output.json", encoding="utf-8") as f:
        screener_data = json.load(f)

    candidates = screener_data["flatResults"]
    print(f"재무분석 대상(1주 보합 통과) 종목 수: {len(candidates)}")

    passed = run_financial_analysis(candidates)

    print(f"\n총 {len(candidates)}개 후보 중 재무 가치 유지 종목: {len(passed)}개")

    save_results_json(passed)
