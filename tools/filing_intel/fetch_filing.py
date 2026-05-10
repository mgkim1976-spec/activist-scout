"""DART majorstock + document.xml 다운로드 + 본문 텍스트 추출.

핵심 함수:
  list_majorstock(corp_code)              → 5%+ 신고 목록
  fetch_document_text(rcept_no)           → 본문 ZIP → XML → 텍스트
  fetch_majorstock_meta(rcept_no, corp_code) → 메타 한 건 조회 (보고자/지분/날짜)
"""
from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import requests

from activist_scout.config import DART_API_KEY
from activist_scout.utils import dart_get


DART_DOC_URL = "https://opendart.fss.or.kr/api/document.xml"


def list_majorstock(corp_code: str) -> list[dict[str, Any]]:
    """특정 회사의 5%+ 대량보유 공시 전체 목록."""
    data = dart_get("majorstock.json", {"corp_code": corp_code})
    if not data or data.get("status") != "000":
        return []
    return list(data.get("list", []))


def fetch_majorstock_meta(rcept_no: str, corp_code: str) -> dict[str, Any] | None:
    """rcept_no 와 corp_code 가 일치하는 majorstock 항목 한 건 반환."""
    for item in list_majorstock(corp_code):
        if item.get("rcept_no") == rcept_no:
            return item
    return None


def fetch_document_text(rcept_no: str, *, timeout: int = 60) -> str:
    """DART document.xml ZIP → 가장 큰 XML → 태그 제거 텍스트.

    파일이 ZIP 이 아닌 경우 (예: 암호화 PDF) 빈 문자열 반환.
    """
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY 미설정")
    resp = requests.get(
        DART_DOC_URL,
        params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no},
        timeout=timeout,
    )
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile:
        return ""

    biggest = max(zf.namelist(), key=lambda n: zf.getinfo(n).file_size)
    raw = zf.read(biggest).decode("utf-8", errors="replace")
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def slice_around(text: str, keywords: list[str], window: int = 600) -> dict[str, str]:
    """본문에서 각 키워드 주변 ±window 자만 추출 (LLM 토큰 절감용)."""
    out: dict[str, str] = {}
    for kw in keywords:
        m = re.search(kw, text)
        if not m:
            out[kw] = ""
            continue
        s = max(0, m.start() - 50)
        e = min(len(text), m.end() + window)
        out[kw] = text[s:e]
    return out


# 대량보유 보고서에서 핵심 정보가 모여 있는 키워드 (Gemini 토큰 절감용)
DEFAULT_KEYWORDS = [
    "보유목적",
    "보고사유",
    "취득자금등의 개요",
    "취득자금등의 조성경위",
    "보유주식등의 수 및 보유비율",
    "발행회사와의 관계",
    "특별관계자",
    "보고자 개요",
    "차입",
    "거래종결",
    "선행조건",
]
