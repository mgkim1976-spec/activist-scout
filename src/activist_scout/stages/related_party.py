"""
사업보고서 §X (특수관계자와의 거래) 텍스트를 LLM(Gemini)으로 분석해
*일감몰아주기 정량화* — 매출 비중을 추출.

입력: enriched.json 의 `related_party_section` 필드 (enrich_dart.py --report-text 로 채워짐)
출력: enriched.json 에 `related_party_analysis` 필드 추가
       {
         "related_party_sales_won": ...,
         "total_revenue_won": ...,
         "ratio_pct": 12.3,
         "evidence": "...",   # LLM 인용 텍스트
       }

사용법:
  python enrich_dart.py --report-text   # 먼저 텍스트 추출
  python parse_related_party.py         # LLM 분석

비고:
- universe ~50종목 × Gemini 1 call ≈ 5분
- LLM 비용: 종목당 텍스트 12,000자 + 응답 ≈ 입력 4k + 출력 0.5k token
"""
from __future__ import annotations

import json
import time

from google import genai
from google.genai import types

from activist_scout.config import ENRICHED_JSON, GEMINI_API_KEY, GEMINI_MODEL, require


SYSTEM = """당신은 한국 사업보고서 §X "특수관계자와의 거래" 섹션 텍스트를 분석하여
일감몰아주기(특수관계인 거래) 매출 비중을 추출하는 분석가입니다.

목표:
- 회사가 *특수관계인(자회사·계열사·임원/대주주 친인척이 운영하는 회사)에게* 판매한 매출 합계
- 회사 *총매출(영업수익)* 대비 비중 (%)
- 비중 ≥ 10% 면 활동주의 펀드의 일감몰아주기 공격 타깃

추출 규칙:
- "특수관계자에게 매출" 또는 "거래종류: 매출/수익" 의 합계
- "전체 특수관계자 합계" 행이 있으면 우선 사용
- 단위는 텍스트에서 명시 (천원/백만원/원)
- 총매출은 같은 보고서의 회사 매출액 (없으면 텍스트에서 추정 안함, total_revenue_won=0 으로 표시)
- ratio_pct = related_party_sales_won / total_revenue_won × 100
- 추출 불가 시 confidence: low + ratio_pct: 0

evidence는 텍스트의 가장 중요한 한 문장 인용 (반드시 텍스트에 실제 존재).
모든 출력은 JSON schema 준수, 추측 금지."""


SCHEMA = {
    "type": "object",
    "properties": {
        "related_party_sales_won": {"type": "number"},
        "total_revenue_won": {"type": "number"},
        "ratio_pct": {"type": "number"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string"},
    },
    "required": ["related_party_sales_won", "total_revenue_won",
                 "ratio_pct", "confidence", "evidence"],
}


def parse_one(client, text: str, name: str) -> dict | None:
    if not text or len(text) < 100:
        return None
    user = (
        f"회사명: {name}\n\n"
        f"사업보고서 §X 텍스트 (max 12,000자):\n{text[:12000]}\n\n"
        "위 텍스트에서 특수관계자 매출, 총매출, 비중(%)을 추출하여 schema에 맞게 응답하세요."
    )
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_schema=SCHEMA,
                temperature=0.1,
            ),
        )
        return json.loads(resp.text)
    except Exception as e:
        print(f"  ! {name}: LLM 호출 실패 ({e})")
        return None


def main():
    require("GEMINI_API_KEY")
    enr = json.load(open(ENRICHED_JSON, encoding="utf-8"))
    targets = [(tk, d) for tk, d in enr.items()
               if d.get("related_party_section")]
    print(f"분석 대상: {len(targets)}개 (related_party_section 있는 종목만)")
    if not targets:
        print("⚠️ related_party_section 없음. enrich_dart.py --report-text 먼저 실행하세요.")
        return

    client = genai.Client(api_key=GEMINI_API_KEY)
    for i, (tk, d) in enumerate(targets, 1):
        result = parse_one(client, d["related_party_section"], d["name"])
        if result:
            # Post-process: 사업보고서 §X에는 보통 *총매출* 이 없음 (회사 매출은 §I/§II)
            # → fundamentals.매출_4Y 의 최근 값으로 ratio 재계산
            f = d.get("fundamentals") or {}
            rev_str = f.get("매출_4Y(억원)") or ""
            try:
                rev_list = json.loads(rev_str.replace("'", '"')) if isinstance(rev_str, str) else rev_str
                latest_rev_won = float(rev_list[0]) * 1e8 if rev_list else 0
            except Exception:
                latest_rev_won = 0
            sales_won = float(result.get("related_party_sales_won") or 0)
            if latest_rev_won > 0 and sales_won > 0:
                ratio_recompute = round(sales_won / latest_rev_won * 100, 1)
                result["total_revenue_won_yfinance"] = latest_rev_won
                result["ratio_pct_yfinance"] = ratio_recompute
                # ratio_pct 가 LLM에서 0인 경우 yfinance 기반으로 대체
                if (result.get("ratio_pct") or 0) == 0:
                    result["ratio_pct"] = ratio_recompute
                    result["confidence"] = "medium"   # yfinance 매출은 신뢰
                    result["confidence_adj"] = "post_processed_yfinance_rev"

            d["related_party_analysis"] = result
            ratio = result.get("ratio_pct", 0)
            tag = "🚨" if ratio >= 10 else ("⚠️" if ratio >= 5 else "✓")
            print(f"  {tag} {tk} {d['name']:14s} ratio={ratio:>5.1f}% "
                  f"(매출 {sales_won/1e8:>5.0f}억 / "
                  f"총 {latest_rev_won/1e8:>5.0f}억) "
                  f"[{result.get('confidence','?')}]")
        if i % 10 == 0:
            print(f"  --- 진행 {i}/{len(targets)} ---")
        time.sleep(0.3)

    with open(ENRICHED_JSON, "w", encoding="utf-8") as f:
        json.dump(enr, f, ensure_ascii=False, indent=2)
    high_ratio = sum(1 for tk, d in enr.items()
                     if (d.get("related_party_analysis") or {}).get("ratio_pct", 0) >= 10)
    print(f"\n저장: {ENRICHED_JSON}")
    print(f"  - related_party_analysis 부착: {sum(1 for d in enr.values() if d.get('related_party_analysis'))}개")
    print(f"  - 일감 비중 ≥ 10% 종목: {high_ratio}개")


if __name__ == "__main__":
    main()
