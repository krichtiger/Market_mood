"""
5단계: 최근 3개월 뉴스를 검색해 하락 요인/투자적합성을 Claude API로 판단한다.

전제:
- ANTHROPIC_API_KEY 환경변수 필요
- 입력: 4단계(재무분석)를 통과한 종목 리스트
  (국내 financial_analysis_kr_output.json + 해외 financial_analysis_us_output.json 의 "passed")
- 종목별로 구글 뉴스 RSS에서 최근 3개월 헤드라인을 모아, Claude에게 "하락 요인"과
  "투자적합성"을 판단시킨다. (구글 뉴스 RSS는 별도 API 키 없이 사용 가능)
"""

import json
import os
import time
import datetime
import urllib.parse
import xml.etree.ElementTree as ET

import requests
import anthropic

ANTHROPIC_MODEL = "claude-opus-5"
NEWS_LOOKBACK_DAYS = 90       # 최근 3개월
MAX_ARTICLES_PER_STOCK = 15
REQUEST_DELAY_SEC = 0.5

client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None


def fetch_recent_news(query: str, lang: str):
    """
    구글 뉴스 RSS에서 최근 NEWS_LOOKBACK_DAYS일 이내 기사 제목 목록을 가져온다.
    lang: 'ko'(국내) 또는 'en'(해외)
    """
    if lang == "ko":
        locale_params = {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    else:
        locale_params = {"hl": "en-US", "gl": "US", "ceid": "US:en"}

    q = f"{query} when:{NEWS_LOOKBACK_DAYS}d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, **locale_params})

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"[WARN] 뉴스 조회 실패 ({query}): {e}")
        return []

    articles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if title:
            articles.append({"title": title, "pub_date": pub_date})
        if len(articles) >= MAX_ARTICLES_PER_STOCK:
            break

    return articles


def _parse_claude_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    return json.loads(text.strip())


def analyze_with_claude(name: str, code: str, articles: list, financial_summary: str):
    """뉴스 헤드라인 + 재무 요약을 Claude에 전달해 하락 요인/투자적합성 판단"""
    if not articles:
        headline_text = "(최근 3개월 뉴스 없음)"
    else:
        headline_text = "\n".join(f"- [{a['pub_date']}] {a['title']}" for a in articles)

    prompt = f"""다음은 최근 고점 대비 30% 이상 하락했다가 최근 1주일간 보합 중인 종목입니다.
재무제표상으로는 뚜렷한 가치 훼손이 확인되지 않았습니다.

종목: {name} ({code})
재무 요약: {financial_summary}

최근 3개월 뉴스 헤드라인:
{headline_text}

위 정보를 바탕으로 다음을 판단해주세요:
1. 주가 하락의 주된 요인이 무엇으로 보이는지 (뉴스에서 확인되는 구체적 사건/이슈)
2. 그 하락 요인이 일시적/외부적인지, 아니면 기업의 근본 경쟁력 훼손인지
3. 현재 시점에서 투자적합(저평가 기회로 볼 수 있음) 여부를 true/false로 판단하고 한 문장 근거

JSON으로만 답하세요:
{{"decline_reason": "...", "reason_type": "일시적 또는 구조적", "investable": true또는false, "verdict_reason": "..."}}"""

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return _parse_claude_json(text)
    except (json.JSONDecodeError, IndexError):
        return {
            "decline_reason": text[:200],
            "reason_type": "알수없음",
            "investable": False,
            "verdict_reason": "응답 파싱 실패",
        }


def run_news_analysis(candidates, lang: str):
    """
    candidates: financial_analysis 통과 종목 리스트 (code/name/market 포함)
    lang: 'ko' 또는 'en' (뉴스 검색 언어)
    """
    results = []
    for c in candidates:
        articles = fetch_recent_news(c["name"], lang)
        financial_summary = c.get("reason", "")

        verdict = analyze_with_claude(c["name"], c["code"], articles, financial_summary)

        merged = {**c, **verdict, "news_count": len(articles)}
        results.append(merged)

        status = "투자적합" if verdict.get("investable") else "제외"
        print(f"  [{status}] {c['name']}({c['code']}) - {str(verdict.get('decline_reason', ''))[:60]}")

        time.sleep(REQUEST_DELAY_SEC)

    return results


def save_results_json(results, path="news_analysis_output.json"):
    data = {
        "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M 기준"),
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{path} 생성 완료")


def _load_passed(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["passed"]
    except FileNotFoundError:
        return []


if __name__ == "__main__":
    if client is None:
        print("[WARN] ANTHROPIC_API_KEY가 설정되지 않아 뉴스 분석을 건너뜁니다.")
        save_results_json([])
    else:
        kr_passed = _load_passed("financial_analysis_kr_output.json")
        us_passed = _load_passed("financial_analysis_us_output.json")

        print(f"국내 재무분석 통과: {len(kr_passed)}개, 해외 재무분석 통과: {len(us_passed)}개")

        kr_results = run_news_analysis(kr_passed, lang="ko")
        us_results = run_news_analysis(us_passed, lang="en")

        all_results = kr_results + us_results
        investable = [r for r in all_results if r.get("investable")]

        print(f"\n총 {len(all_results)}개 종목 중 투자적합 판단: {len(investable)}개")

        save_results_json(all_results)
