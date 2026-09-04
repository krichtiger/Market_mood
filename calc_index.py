"""
공포탐욕지수 계산 스크립트 (v2 — 재시도/오류내성 추가)
------------------------------------------------------------
pip install pykrx yfinance pandas
python calc_index.py
------------------------------------------------------------
"""

import json
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock as krx
import yfinance as yf


def with_retry(func, *args, tries=3, delay=3, **kwargs):
    """일시적인 데이터 소스 오류에 대비해 재시도."""
    last_err = None
    for i in range(tries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            print(f"  ...실패 ({i+1}/{tries}): {e}")
            time.sleep(delay)
    print(f"  ...최종 실패, 이 항목은 건너뜁니다: {last_err}")
    return None
