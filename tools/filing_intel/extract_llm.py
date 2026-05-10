"""Gemini structured output 으로 대량보유 본문 → JSON 구조화.

본문 전체(보통 100K+ chars)를 LLM 에 넣지 않고 fetch_filing.slice_around 로
핵심 키워드 주변만 추출한 ~6~10K chars 만 입력 → 토큰 비용 절감.

⚠️ LLM 환각 가능. evidence 필드에 본문 인용을 강제하고,
confidence 가 low/medium 인 결과는 사람 검증 권장.
"""
from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from activist_scout.config import GEMINI_API_KEY


GEMINI_MODEL = "gemini-2.5-pro"

SYSTEM_INSTRUCTION = """당신은 한국 자본시장 공시 분석가입니다.
주식 등의 대량보유 보고서 (5%+ 신고) 본문 발췌에서 핵심 정보를 추출합니다.

규칙:
1. 추측 금지. 본문에 명시되지 않은 사실은 null 또는 빈 문자열로.
2. evidence 필드에 추출 근거가 된 본문 문장을 인용 (15~80자).
3. 보유목적은 DART 표준 카테고리에 정확히 매핑 ("경영권 영향" / "단순투자" / "일반투자" / "기타").
4. 금액 단위는 원(KRW) 정수로 변환. "1,452억" → 145200000000.
5. confidence: 본문에 핵심 정보가 명확히 있으면 high, 일부만이면 medium, 추론 비중이 크면 low.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "발행회사": {"type": "string"},
        "발행회사_종목코드": {"type": "string"},
        "보고자_명칭": {"type": "string"},
        "보고자_구분": {
            "type": "string",
            "enum": ["개인", "국내법인", "외국법인", "조합", "기타"],
        },
        "발행회사와의_관계": {"type": "string"},
        "보고구분": {
            "type": "string",
            "enum": ["신규", "변경", "변동", "변동ㆍ변경", "변동·변경", "기타"],
        },
        "보유목적": {
            "type": "string",
            "enum": ["경영권 영향", "단순투자", "일반투자", "기타"],
        },
        "보고사유": {"type": "string"},
        "직전_보유주식수": {"type": "integer"},
        "이번_보유주식수": {"type": "integer"},
        "직전_보유비율_pct": {"type": "number"},
        "이번_보유비율_pct": {"type": "number"},
        "취득금액_원": {"type": "integer"},
        "취득자금_자기자금_원": {"type": "integer"},
        "취득자금_차입금_원": {"type": "integer"},
        "취득자금_기타_원": {"type": "integer"},
        "취득자금_조성_경위": {"type": "string"},
        "차입처": {"type": "string"},
        "거래종결_조건": {"type": "string"},
        "특별관계자_명단": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["보유목적", "보고구분", "보고사유", "evidence", "confidence"],
}


def extract_from_text(slices: dict[str, str], full_head: str = "") -> dict[str, Any] | None:
    """본문 키워드 슬라이스 + 본문 앞부분 → 구조화 JSON.

    slices: fetch_filing.slice_around 결과
    full_head: 본문 앞 2000자 정도 (메타데이터 보강)
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 미설정")

    parts = []
    if full_head:
        parts.append(f"[본문 앞부분 발췌]\n{full_head[:2000]}")
    for kw, snip in slices.items():
        if snip:
            parts.append(f"[키워드 '{kw}' 주변]\n{snip}")
    user = (
        "다음은 DART 주식등의 대량보유 보고서 본문 발췌입니다. "
        "schema 에 맞춰 구조화하여 응답하세요.\n\n"
        + "\n\n---\n\n".join(parts)
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=SCHEMA,
                temperature=0.1,
            ),
        )
        return json.loads(resp.text)
    except Exception as e:
        print(f"  ! extract_llm 실패: {e}")
        return None
