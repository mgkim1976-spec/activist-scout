"""enriched.json → Gemini 2.5 Pro로 가치함정 유형 분류."""
from __future__ import annotations

import csv
import json
import time

from google import genai
from google.genai import types

from activist_scout.config import (
    CLASSIFICATION_CSV, CLASSIFICATION_JSON, ENRICHED_JSON,
    GEMINI_API_KEY, GEMINI_MODEL, require,
)


SYSTEM = """당신은 한국 행동주의 헤지펀드의 시니어 애널리스트입니다.
밸류업, 코리아 디스카운트, 지배구조 록인, 단일고객 의존성, 행동주의 캠페인 메커니즘을 깊이 이해합니다.

데이터에 다음이 사전 분류되어 있으니 적극 활용:
- `summary.filer_count`: 5%+ 보고자 유형별 카운트 (activist/semi_activist/passive/pe_fund/strategic/individual)
- `summary.recent_activist_filings_12M`: 최근 12개월 행동주의/PE 신규 filing 건수
- `summary.treasury_score`: 자사주 공시 가중합 (양수=소각/매입 우세, 음수=처분/신탁해지 우세)
- `summary.treasury_dir_count`: 방향성별 자사주 공시 카운트
- `liquidity.capacity_score`: 5% 매집 capacity (1=≤30일, 0=≥90일)
- `fundamentals.잉여자본비율`: 순현금/시총 (>0.30이면 행동주의 핵심 타깃)

각 종목에 대해 다음 7개 카테고리 중 ONE primary_trap_type을 부여:

1. 승계록인 (Succession Lock-in) — 오너+특수관계인 ≥60% AND 자사주 소각/배당 빈약 AND treasury_score≤0
2. 신뢰록인 (Trust Lock-in) — 배임/횡령/회계 한정/거래정지 이력 (raw 공시명에서 발견 시)
3. 협상력록인 (Bargaining-power Lock-in) — 자동차부품/조선기자재 등 단일거대고객, 매출↑인데 OPM 정체
4. 행동주의후보 (Activism Candidate) — recent_activist_filings_12M ≥1 AND 오너 40~55% AND 잉여자본>0.15
5. 패시브플로우 (Passive Flow) — 기관 매수 일관 BUT activist filing 없음, filer_count는 passive 위주
6. 정상가치주 (Normal Value) — 드뭄
7. 기타 (Other) — 분류 어려운 경우만

action 기준:
- AVOID: 록인 강·촉매 부재 OR liquidity.capacity_score<0.3 (행동주의 진입 불가)
- WATCH: 잠재 촉매 있으나 시점/유동성 보류
- VERIFY: 추가 데이터 필요
- CONSIDER: 다중 양의 신호 + capacity≥0.5 + 잉여자본 충분
- BUY: 강한 촉매 + 안전마진 + capacity≥0.7 + 행동주의 신규 filing (드뭄)

페이오프 추정:
- est_upside_pct: 캠페인 성공 시 12~24개월 예상 수익률 (0~100, %).
  근거: 잉여자본비율, 자사주 소각 가능 규모, peer 평균 PBR과의 갭.
- est_downside_pct: 가치함정 지속 시 다운사이드 (0~80, %). 근거: 산업 시클리컬 변동, 기존 trap_strength.
- holding_period_months: 6~30. 행동주의 캠페인 평균 사이클 12~18.

***중요***: trap_strength, activist_probability는 반드시 [0.0, 1.0] 범위.
est_upside_pct, est_downside_pct는 [0, 100] 범위.
한국어로 narrative와 key_signals 작성. 데이터에 없는 사실 추측 금지."""


CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "name": {"type": "string"},
                    "primary_trap_type": {
                        "type": "string",
                        "enum": ["승계록인", "신뢰록인", "협상력록인",
                                 "행동주의후보", "패시브플로우", "정상가치주", "기타"],
                    },
                    "trap_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "activist_probability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "action": {
                        "type": "string",
                        "enum": ["AVOID", "WATCH", "VERIFY", "CONSIDER", "BUY"],
                    },
                    "est_upside_pct": {"type": "number", "minimum": 0, "maximum": 100},
                    "est_downside_pct": {"type": "number", "minimum": 0, "maximum": 80},
                    "holding_period_months": {"type": "integer", "minimum": 6, "maximum": 30},
                    "key_signals": {"type": "array", "items": {"type": "string"}},
                    "narrative": {"type": "string"},
                },
                "required": ["ticker", "name", "primary_trap_type", "trap_strength",
                             "activist_probability", "action",
                             "est_upside_pct", "est_downside_pct", "holding_period_months",
                             "key_signals", "narrative"],
            },
        }
    },
    "required": ["results"],
}


def compact(d: dict) -> dict:
    f = d.get("fundamentals", {})
    flow = d.get("flow", {})
    liq = d.get("liquidity", {})
    summary = d.get("summary", {})
    co = d.get("company") or {}
    return {
        "ticker": d["ticker"],
        "name": d["name"],
        "industry_code": co.get("induty_code"),
        "ceo": co.get("ceo_nm"),
        "PBR": f.get("PBR"), "PER": f.get("PER"),
        "시총_억": f.get("시가총액(억)"),
        "최대주주_지분율_pct": f.get("최대주주_지분율(%)"),
        "잉여자본비율": f.get("잉여자본비율"),
        "순현금_억": f.get("순현금(억)"),
        "영업이익_최근3Q_백만": f.get("영업이익_3Q(백만원)"),
        "매출_4년_억": f.get("매출_4Y(억원)"),
        "기관순매수_90D_억": flow.get("기관순매수_90D(억)"),
        "기관순매수_20D_억": flow.get("기관순매수_20D(억)"),
        "매수일_비율_90D": flow.get("순매수일/총_90D"),
        "현재가_vs_매수VWAP_90D_pct": flow.get("vs_매수VWAP_90D(%)"),
        "liquidity": {
            "ADV_20D_억": liq.get("ADV_20D(억)"),
            "days_to_5pct": liq.get("days_to_5pct"),
            "capacity_score": liq.get("capacity_score"),
        },
        "summary": {
            "treasury_score": summary.get("treasury_score"),
            "treasury_dir_count": summary.get("treasury_dir_count"),
            "filer_count": summary.get("filer_count"),
            "recent_activist_filings_12M": summary.get("recent_activist_filings_12M"),
            "recent_activists": summary.get("recent_activists", []),
        },
        "treasury_disclosures_recent": d.get("treasury_disclosures", [])[:5],
        "major_holdings_5pct_recent": [
            x for x in d.get("major_holdings_5pct", []) if x.get("rcept_dt", "") >= "20250101"
        ][:8],
        "exec_changes_recent": d.get("exec_holdings", [])[:5],
    }


def classify_batch(client, items: list[dict], retries: int = 3) -> list[dict]:
    user = (
        "다음 한국 KOSPI 종목들을 가치함정 유형으로 분류해 주세요. "
        "5%+ 보유자 명단(repror)이 있으면 펀드/투자조합인지 식별하세요.\n\n"
        f"DATA:\n{json.dumps(items, ensure_ascii=False, indent=2)}"
    )
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    response_mime_type="application/json",
                    response_schema=CLASSIFY_SCHEMA,
                    temperature=0.2,
                ),
            )
            return json.loads(resp.text)["results"]
        except Exception as e:
            print(f"  ! Gemini 호출 실패 ({attempt + 1}/{retries}): {e}")
            time.sleep(2 ** attempt)
    return []


def main():
    require("GEMINI_API_KEY")
    client = genai.Client(api_key=GEMINI_API_KEY)

    with open(ENRICHED_JSON, encoding="utf-8") as f:
        enriched = json.load(f)
    items = [compact(d) for d in enriched.values()]
    print(f"분류 대상: {len(items)}개 (모델: {GEMINI_MODEL})")

    BATCH = 8
    all_results = []
    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        print(f"  배치 {i // BATCH + 1}/{(len(items) + BATCH - 1) // BATCH} ({len(batch)}개)")
        all_results.extend(classify_batch(client, batch))
        time.sleep(1)

    with open(CLASSIFICATION_JSON, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"저장: {CLASSIFICATION_JSON} ({len(all_results)}개)")

    fields = ["ticker", "name", "primary_trap_type", "trap_strength",
              "activist_probability", "action",
              "est_upside_pct", "est_downside_pct", "holding_period_months",
              "expected_value_pct",
              "narrative", "key_signals_joined"]
    with open(CLASSIFICATION_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_results:
            p = r["activist_probability"]
            up = r.get("est_upside_pct", 0)
            dn = r.get("est_downside_pct", 0)
            ev = round(p * up - (1 - p) * dn, 1)
            w.writerow({
                "ticker": r["ticker"],
                "name": r["name"],
                "primary_trap_type": r["primary_trap_type"],
                "trap_strength": round(r["trap_strength"], 2),
                "activist_probability": round(p, 2),
                "action": r["action"],
                "est_upside_pct": up,
                "est_downside_pct": dn,
                "holding_period_months": r.get("holding_period_months", 12),
                "expected_value_pct": ev,
                "narrative": r["narrative"],
                "key_signals_joined": " | ".join(r.get("key_signals", [])),
            })
    print(f"저장: {CLASSIFICATION_CSV}")


if __name__ == "__main__":
    main()
