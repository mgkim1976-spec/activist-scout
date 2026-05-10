"""Gemini Google search grounding 으로 5%+ 신고 보강.

DART 공시는 *법적 사실* 만 담는다. 거래 동기 / 종결일 / 시너지 / 시장 반응은
언론 보도와 보도자료에서만 확인 가능. Gemini 2.5 Pro + Google Search 로
대량보유 신고에 대한 *대중적 인식 / 추가 사실* 을 한 번에 수집.

⚠️ Google grounding 도 환각 / 가짜 출처 위험 있음. 출처 URL 반드시 보고서에
명시하여 사용자 검증 가능하도록.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

from activist_scout.config import GEMINI_API_KEY


GEMINI_MODEL = "gemini-2.5-pro"


@dataclass
class GroundingResult:
    text: str
    queries: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)


def ground_filing(
    *,
    issuer_name: str,
    issuer_ticker: str,
    filer_name: str,
    parent_listed: str | None,
    holding_purpose: str,
    file_date: str,
    extra_context: str = "",
) -> GroundingResult | None:
    """5%+ 신고에 대한 언론·시장 사실을 Google grounding 으로 수집.

    parent_listed: 비상장 보고자의 상장 모회사 (있으면 그룹 단위로 검색됨)
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 미설정")

    filer_label = filer_name
    if parent_listed:
        filer_label = f"{filer_name} (상장 모회사: {parent_listed})"

    prompt = f"""한국어로 답하라. Google Search 로 사실 검증.

검증 대상:
- 발행회사: {issuer_name} (종목코드 {issuer_ticker})
- 보고자: {filer_label}
- 보유목적: {holding_purpose}
- 신고일: {file_date}
{extra_context}

다음 5가지를 *언론 보도 또는 공시 보도자료* 기반으로 답하라. 사실 미확인이면 "확인 안 됨" 명시.

1. **언론 보도 여부**: 보도 시점과 핵심 헤드라인 2~3건 인용.
2. **거래 종결일/결제일**: 공시 또는 보도에 명시된 종결 예정일.
3. **인수자의 전략적 동기**: 산업 시너지 / 사업 확장 / 그룹 재편 등의 *공식 설명*.
4. **인수 후 계획**: 합병 / 공개매수 / 상장폐지 / 자회사 유지 관련 *공식 발표*.
5. **인수자의 과거 M&A 패턴** (있으면): *행동주의 / 산업 통합 / 그룹 강화* 중 어느 카테고리.

각 항목은 *출처명과 날짜* 를 본문에 포함하라.
"""

    client = genai.Client(api_key=GEMINI_API_KEY)
    import time
    resp = None
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.1,
                ),
            )
            break
        except Exception as e:
            if attempt < 2:
                print(f"  ! grounding 시도 {attempt+1}/3 실패 ({e}) — {2*(attempt+1)}초 후 재시도")
                time.sleep(2 * (attempt + 1))
            else:
                print(f"  ! grounding 최종 실패: {e}")
                return None
    if resp is None:
        return None

    queries = []
    sources = []
    if resp.candidates:
        gm = resp.candidates[0].grounding_metadata
        if gm:
            queries = list(getattr(gm, "web_search_queries", None) or [])
            for c in getattr(gm, "grounding_chunks", None) or []:
                web = getattr(c, "web", None)
                if web:
                    sources.append({"title": web.title or "", "uri": web.uri or ""})

    return GroundingResult(text=resp.text or "", queries=queries, sources=sources)
