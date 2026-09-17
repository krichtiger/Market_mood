"""
6단계: 5단계까지 통과한 종목들 중 투자가치 상위 10개를 추려 종합 리포트를 작성하고
텔레그램으로 발송한다. (파이프라인의 최종 산출물 — 이 리포트 1건만 텔레그램으로 나간다)

전제:
- ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수 필요
- 입력: news_analysis_output.json의 results 중 investable == true인 종목들
"""

import json
import os
import datetime

import requests
import anthropic

ANTHROPIC_MODEL = "claude-opus-5"
TOP_N = 10
TELEGRAM_MESSAGE_LIMIT = 4096

client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None


def build_fallback_report(candidates):
    """ANTHROPIC_API_KEY가 없을 때, LLM 없이 원본 데이터만으로 간단한 리포트 생성"""
    lines = [
        f"📊 이번 주 저평가 후보 종목 — {datetime.datetime.now().strftime('%Y-%m-%d')}",
        f"(ANTHROPIC_API_KEY 미설정으로 AI 요약 없이 원본 데이터만 표시, 총 {len(candidates)}개)",
        "",
    ]
    for i, c in enumerate(candidates[:TOP_N], 1):
        lines.append(
            f"{i}. {c['name']}({c['code']}, {c['market']}) 고점대비 -{c['drop_ratio']}%\n"
            f"   재무: {c.get('reason', '')}\n"
            f"   하락요인: {c.get('decline_reason', '')}"
        )
    return "\n".join(lines)


def build_report(candidates):
    """투자적합 판단된 종목들을 Claude에 전달해 상위 TOP_N 선별 + 리포트 작성"""
    if client is None:
        print("[WARN] ANTHROPIC_API_KEY가 설정되지 않아 AI 리포트 대신 간단 요약으로 대체합니다.")
        return build_fallback_report(candidates)

    candidates_text = "\n\n".join(
        f"- {c['name']}({c['code']}, {c['market']})\n"
        f"  고점대비 하락률: -{c['drop_ratio']}%\n"
        f"  최근1주 변동성: {c.get('recent_volatility_pct')}%\n"
        f"  재무: {c.get('reason', '')}\n"
        f"  하락요인: {c.get('decline_reason', '')} ({c.get('reason_type', '')})\n"
        f"  판단근거: {c.get('verdict_reason', '')}"
        for c in candidates
    )

    prompt = f"""아래는 [1) 고점대비 -30% 하락 -> 2) 최근 1주 보합 -> 3) 재무 가치 유지 확인 ->
4) 뉴스 분석상 투자적합] 4단계 필터를 모두 통과한 종목 목록입니다.

{candidates_text}

이 중에서 투자가치가 가장 높다고 판단되는 상위 최대 {TOP_N}개를 선별하고,
텔레그램으로 발송할 한국어 리포트를 작성해주세요.

형식:
- 맨 위에 "이번 주 저평가 후보 종목" 제목과 총 후보 수
- 종목별로: 순위, 종목명(코드), 시장, 고점대비 하락률, 선정 이유(2~3문장, 재무+뉴스 근거 요약)
- 너무 길지 않게, 텔레그램 메시지로 보내기 적당한 분량(전체 3500자 이내)으로 작성
- 이모지를 적절히 사용해서 가독성 있게

리포트 본문만 출력하세요 (설명이나 JSON 없이 발송할 텍스트 그대로)."""

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return next((b.text for b in response.content if b.type == "text"), "")


def send_telegram(text: str):
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        text = text[: TELEGRAM_MESSAGE_LIMIT - 20] + "\n...(생략)"

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text})
    resp.raise_for_status()
    print("텔레그램 발송 완료")


if __name__ == "__main__":
    with open("news_analysis_output.json", encoding="utf-8") as f:
        data = json.load(f)

    investable = [r for r in data["results"] if r.get("investable")]
    print(f"투자적합 판단 종목 수: {len(investable)}개")

    if not investable:
        report_text = (
            f"📊 주간 저평가 후보 리포트 — {datetime.datetime.now().strftime('%Y-%m-%d')}\n\n"
            "이번 주는 1~5단계 필터를 모두 통과한 종목이 없었습니다."
        )
    else:
        report_text = build_report(investable)

    print("\n--- 최종 리포트 ---")
    print(report_text)

    with open("final_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)

    send_telegram(report_text)
