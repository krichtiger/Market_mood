"""
4단계(국내): DART Open API로 재무제표를 조회해 "기업가치 변동" 여부를 판단한다.

전제:
- DART_API_KEY 환경변수 필요 (https://opendart.fss.or.kr 에서 무료 발급)
- 입력: screener_output.json의 flatResults (고점 대비 -30% 하락 + 최근 1주 보합 종목)
- 매출액/영업이익 YoY 증감률과 부채비율을 보고, 실적이 크게 악화된 종목은
  "가치 하락(하락에 정당한 이유 있음)"으로 판단해 제외하고, 그렇지 않은 종목만
  다음 단계(뉴스 분석)로 넘긴다.

판단 기준 (1차 버전, 규칙 기반):
- 매출액 YoY 증감률 < REVENUE_DECLINE_THRESHOLD 이거나
  영업이익 YoY 증감률 < OPERATING_INCOME_DECLINE_THRESHOLD 이면 "가치 하락"
- 부채비율(부채총계/자본총계 * 100) > DEBT_RATIO_THRESHOLD 이면 "가치 하락"
- 위 조건에 해당 없으면 "가치 유지"로 보고 통과

주의:
- DART_API_KEY가 없으면 이 단계는 건너뛰고 빈 리스트를 반환한다 (파이프라인 전체를
  막지 않기 위함). 국내 종목은 이 키가 등록되기 전까지 4단계 이후로 진행되지 않는다.
- DART API 응답 필드(account_nm/thstrm_amount 등)는 문서 기준으로 작성했으며,
  실제 키 등록 후 첫 실행에서 계정과목명 표기가 다르면 ACCOUNT_NAME_ALIASES에
  후보를 추가해서 대응한다.
"""

import io
import json
import os
import time
import zipfile
import datetime
import xml.etree.ElementTree as ET

import requests

DART_API_KEY = os.environ.get("DART_API_KEY")
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIAL_STATEMENT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

REQUEST_DELAY_SEC = 0.3

REVENUE_DECLINE_THRESHOLD = -20.0            # 매출액 YoY(%)가 이보다 낮으면 가치 하락으로 판단
OPERATING_INCOME_DECLINE_THRESHOLD = -30.0   # 영업이익 YoY(%)가 이보다 낮으면 가치 하락으로 판단
DEBT_RATIO_THRESHOLD = 200.0                 # 부채비율(%)이 이보다 높으면 가치 하락으로 판단

# 사업보고서 -> 3분기 -> 반기 -> 1분기 순으로, 최근 보고서부터 시도
REPORT_CODES = ["11011", "11014", "11012", "11013"]

ACCOUNT_NAME_ALIASES = {
    "revenue": ["매출액", "수익(매출액)", "영업수익"],
    "operating_income": ["영업이익", "영업이익(손실)"],
    "total_assets": ["자산총계"],
    "total_liabilities": ["부채총계"],
    "total_equity": ["자본총계"],
}


def get_corp_code_map():
    """DART 고유번호 목록(zip 내 XML)을 받아서 {종목코드: corp_code} 매핑 생성"""
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY 환경변수가 설정되지 않음")

    resp = requests.get(CORP_CODE_URL, params={"crtfc_key": DART_API_KEY}, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read(zf.namelist()[0])

    root = ET.fromstring(xml_bytes)

    mapping = {}
    for node in root.iter("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_code = (node.findtext("corp_code") or "").strip()
        if stock_code:
            mapping[stock_code] = corp_code
    return mapping


def _fetch_financial_statement(corp_code: str, bsns_year: str):
    """연결(CFS) 우선, 없으면 별도(OFS) 재무제표를 조회. 보고서 종류를 최신 순으로 시도."""
    for reprt_code in REPORT_CODES:
        for fs_div in ("CFS", "OFS"):
            resp = requests.get(
                FINANCIAL_STATEMENT_URL,
                params={
                    "crtfc_key": DART_API_KEY,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": reprt_code,
                    "fs_div": fs_div,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "000" and data.get("list"):
                return data["list"]
            time.sleep(REQUEST_DELAY_SEC)
    return []


def _parse_amount(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _find_amount(items, field: str, account_names):
    for item in items:
        if item.get("account_nm") in account_names:
            return _parse_amount(item.get(field))
    return None


def analyze_financial_value(code: str, corp_code: str):
    """종목 하나에 대해 최근 사업연도 재무제표를 조회하고 가치 변동 여부를 판단"""
    this_year = datetime.date.today().year
    # 사업보고서는 다음 해 3월경 공시되므로, 최근 완료 회계연도부터 최대 2년 전까지 시도
    items = None
    bsns_year = None
    for year_offset in (1, 2):
        bsns_year = str(this_year - year_offset)
        items = _fetch_financial_statement(corp_code, bsns_year)
        if items:
            break

    if not items:
        return {"code": code, "value_maintained": None, "reason": "재무제표 조회 실패(데이터 없음)"}

    revenue = _find_amount(items, "thstrm_amount", ACCOUNT_NAME_ALIASES["revenue"])
    revenue_prev = _find_amount(items, "frmtrm_amount", ACCOUNT_NAME_ALIASES["revenue"])
    operating_income = _find_amount(items, "thstrm_amount", ACCOUNT_NAME_ALIASES["operating_income"])
    operating_income_prev = _find_amount(items, "frmtrm_amount", ACCOUNT_NAME_ALIASES["operating_income"])
    total_assets = _find_amount(items, "thstrm_amount", ACCOUNT_NAME_ALIASES["total_assets"])
    total_liabilities = _find_amount(items, "thstrm_amount", ACCOUNT_NAME_ALIASES["total_liabilities"])
    total_equity = _find_amount(items, "thstrm_amount", ACCOUNT_NAME_ALIASES["total_equity"])

    revenue_growth_pct = None
    if revenue is not None and revenue_prev:
        revenue_growth_pct = round((revenue - revenue_prev) / abs(revenue_prev) * 100, 1)

    operating_income_growth_pct = None
    if operating_income is not None and operating_income_prev:
        operating_income_growth_pct = round(
            (operating_income - operating_income_prev) / abs(operating_income_prev) * 100, 1
        )

    debt_ratio_pct = None
    if total_liabilities is not None and total_equity:
        debt_ratio_pct = round(total_liabilities / total_equity * 100, 1)

    reasons = []
    value_maintained = True

    if revenue_growth_pct is not None and revenue_growth_pct < REVENUE_DECLINE_THRESHOLD:
        value_maintained = False
        reasons.append(f"매출액 YoY {revenue_growth_pct}%")

    if (
        operating_income_growth_pct is not None
        and operating_income_growth_pct < OPERATING_INCOME_DECLINE_THRESHOLD
    ):
        value_maintained = False
        reasons.append(f"영업이익 YoY {operating_income_growth_pct}%")

    if debt_ratio_pct is not None and debt_ratio_pct > DEBT_RATIO_THRESHOLD:
        value_maintained = False
        reasons.append(f"부채비율 {debt_ratio_pct}%")

    return {
        "code": code,
        "bsns_year": bsns_year,
        "revenue": revenue,
        "revenue_growth_pct": revenue_growth_pct,
        "operating_income": operating_income,
        "operating_income_growth_pct": operating_income_growth_pct,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "debt_ratio_pct": debt_ratio_pct,
        "value_maintained": value_maintained,
        "reason": "; ".join(reasons) if reasons else "매출/이익 유지, 부채비율 양호",
    }


def run_financial_analysis(candidates):
    """
    candidates: screener_output.json의 flatResults 리스트 (각 dict에 code/name/market 포함)
    가치가 유지된(하락 없는) 종목만 리스트로 반환. DART_API_KEY가 없으면 빈 리스트.
    """
    if not DART_API_KEY:
        print("[WARN] DART_API_KEY가 설정되지 않아 국내 재무분석을 건너뜁니다.")
        return []

    corp_code_map = get_corp_code_map()

    passed = []
    for c in candidates:
        corp_code = corp_code_map.get(c["code"])
        if not corp_code:
            print(f"[SKIP] {c['name']}({c['code']}) DART corp_code 매핑 없음")
            continue

        result = {**c, **analyze_financial_value(c["code"], corp_code)}

        if result["value_maintained"]:
            print(f"  [통과] {c['name']}({c['code']}) - {result['reason']}")
            passed.append(result)
        else:
            print(f"  [제외] {c['name']}({c['code']}) - {result['reason']}")

        time.sleep(REQUEST_DELAY_SEC)

    return passed


def save_results_json(passed, path="financial_analysis_kr_output.json"):
    data = {
        "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M 기준"),
        "passedCount": len(passed),
        "passed": passed,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{path} 생성 완료")


if __name__ == "__main__":
    with open("screener_output.json", encoding="utf-8") as f:
        screener_data = json.load(f)

    candidates = screener_data["flatResults"]
    print(f"재무분석 대상(1주 보합 통과) 종목 수: {len(candidates)}")

    passed = run_financial_analysis(candidates)

    print(f"\n총 {len(candidates)}개 후보 중 재무 가치 유지 종목: {len(passed)}개")

    save_results_json(passed)
