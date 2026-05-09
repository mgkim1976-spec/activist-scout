"""공통 유틸: 영업일 추정, retry 래퍼, corp_code 매핑 로더/빌더, DART 클라이언트."""
from __future__ import annotations

import io
import json
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
import requests
from pykrx import stock

from activist_scout.config import CORP_MAP_FILE, DART_API_KEY


def latest_business_day() -> str:
    """오늘부터 거꾸로 10일 안에서 KOSPI OHLCV가 비어있지 않은 가장 최근 일자."""
    end = datetime.now()
    for i in range(10):
        d = (end - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv(d, market="KOSPI")
            if df is not None and not df.empty and df["거래량"].sum() > 0:
                return d
        except Exception:
            continue
    return end.strftime("%Y%m%d")


def fetch_with_retry(fn: Callable, *args, retries: int = 4, sleep: float = 0.8):
    """pykrx의 빈 DataFrame과 일반 예외를 모두 retry로 처리."""
    for i in range(retries):
        try:
            df = fn(*args)
            if df is not None and not (hasattr(df, "empty") and df.empty):
                return df
        except Exception:
            pass
        time.sleep(sleep * (i + 1))
    return None


# ---- Corp code map ----

def build_corp_code_map() -> None:
    """DART corpCode.xml 다운로드 → stock_code ↔ corp_code 매핑 JSON 빌드.

    분기 1회 실행 권장. `python utils.py --build-corp-map` 또는
    pipeline.py corp_code 스테이지에서 자동 실행.
    """
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY 미설정")
    r = requests.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=120,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read("CORPCODE.xml"))
    mapping = {}
    for item in root.findall("list"):
        sc = (item.findtext("stock_code") or "").strip()
        if not sc or len(sc) != 6:
            continue
        mapping[sc] = {
            "corp_code": (item.findtext("corp_code") or "").strip(),
            "corp_name": (item.findtext("corp_name") or "").strip(),
        }
    with open(CORP_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    print(f"saved {CORP_MAP_FILE} ({len(mapping)} listed companies)")


def load_corp_map() -> dict:
    if not CORP_MAP_FILE.exists():
        raise FileNotFoundError(
            f"{CORP_MAP_FILE} 없음. `python pipeline.py --only corp_code` 먼저 실행하세요."
        )
    with open(CORP_MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


# ---- DART 공용 클라이언트 ----
_DART_SESSION = requests.Session()
_DART_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


def dart_get(path: str, params: dict | None = None, retries: int = 3, timeout: int = 20):
    """DART OpenAPI GET. crtfc_key 자동 주입. 실패 시 None."""
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY 미설정")
    p = dict(params or {})
    p["crtfc_key"] = DART_API_KEY
    url = path if path.startswith("http") else f"https://opendart.fss.or.kr/api/{path.lstrip('/')}"
    for i in range(retries):
        try:
            r = _DART_SESSION.get(url, params=p, timeout=timeout)
            return r.json()
        except Exception:
            time.sleep(0.5 * (i + 1))
    return None


# ---- CLI 진입점 (pipeline.py corp_code 스테이지) ----
if __name__ == "__main__":
    import argparse
    from activist_scout.config import require
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-corp-map", action="store_true",
                        help="DART corpCode.xml 다운로드 → corp_code_map.json 빌드")
    args = parser.parse_args()
    if args.build_corp_map:
        require("DART_API_KEY")
        build_corp_code_map()
    else:
        parser.print_help()
