"""
output.json 내용을 읽어서 텔레그램으로 발송하는 스크립트.
GitHub Actions에서 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수를 넣어 실행합니다.
"""

import json
import os
import requests

with open("output.json", encoding="utf-8") as f:
    data = json.load(f)

lines = [f"📊 마켓 무드 — {data['updatedAt']}", ""]

for m in data["markets"]:
    lines.append(f"{m['name']}: {m['value']}")

lines.append("")
lines.append("국내 업종")
for s in data["krSectors"]:
    lines.append(f"  · {s['name']}: {s['value']}")

lines.append("")
lines.append("미국 업종")
for s in data["usSectors"]:
    lines.append(f"  · {s['name']}: {s['value']}")

text = "\n".join(lines)

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

url = f"https://api.telegram.org/bot{token}/sendMessage"
resp = requests.post(url, data={"chat_id": chat_id, "text": text})
resp.raise_for_status()
print("텔레그램 발송 완료")
