"""
screener_output.json 내용을 읽어서 텔레그램으로 발송하는 스크립트.
GitHub Actions에서 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수를 넣어 실행합니다.

TODO: 현재 daily.yml에서는 호출하지 않음 (중간 결과 발송 대신 아티팩트로만 남김).
파이프라인 3~5단계(재무제표 분석 -> 뉴스 분석 -> 종합 리포트)가 구현되면,
이 스크립트를 종합 리포트 발송 스크립트로 교체하거나 그 안에서 재사용할 것.
"""

import json
import os
import requests

TELEGRAM_MESSAGE_LIMIT = 4096
MAX_LISTED_STOCKS = 40  # 메시지 길이 제한 방어용 상한

with open("screener_output.json", encoding="utf-8") as f:
    data = json.load(f)

flat_results = data["flatResults"]

lines = [
    f"📉 국내 급락+보합 스크리너 — {data['updatedAt']}",
    "",
    f"고점 대비 -30% 이상 하락: {data['totalCount']}개",
    f"그중 최근 1주일가량 보합: {data['flatCount']}개",
    "",
]

for r in flat_results[:MAX_LISTED_STOCKS]:
    lines.append(
        f"· {r['name']}({r['code']}) {r['market']} "
        f"고점대비 -{r['drop_ratio']}% / 1주 변화 {r['recent_change_pct']:+.1f}% "
        f"/ 변동폭 {r['recent_range_pct']:.1f}%"
    )

if len(flat_results) > MAX_LISTED_STOCKS:
    lines.append(f"...외 {len(flat_results) - MAX_LISTED_STOCKS}개 더")

text = "\n".join(lines)

if len(text) > TELEGRAM_MESSAGE_LIMIT:
    text = text[: TELEGRAM_MESSAGE_LIMIT - 20] + "\n...(생략)"

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

url = f"https://api.telegram.org/bot{token}/sendMessage"
resp = requests.post(url, data={"chat_id": chat_id, "text": text})
resp.raise_for_status()
print("텔레그램 발송 완료")
