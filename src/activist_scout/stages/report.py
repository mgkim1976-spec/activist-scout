"""
v5 — scores.json + enriched.json → report.md.

v4의 EV·Beta-binomial calibration을 폐기하고 3축 점수 + tier 기반 ranking으로 재구성.

표시:
- AGM 타임라인 + regime change 경고 (상법 개정으로 historical alpha 비유효)
- Tier 분포 + Tier별 종목 카드
- 3축 점수 detail (어느 룰이 발동했는지 추적 가능)
- LLM narrative 는 보조 정보 (sanity check)
- 백테스트 historical reference (의사결정 입력 X)
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from activist_scout.config import (BACKTEST_ACTIVIST_JSON, ENRICHED_JSON, REPORT_MD,
                    SCORES_JSON, TIER_THRESHOLDS)
from activist_scout.domain import agm_context


TIER_ORDER = [
    "HOT", "WARM", "LATE_SKEPTICAL", "WATCH",
    "LATE_ACCESSIBLE", "LATE_PRICED_IN", "LATE",
    "PASS", "AVOID",
]
TIER_HEADER = {
    "HOT":             "🔥 HOT — 3축 모두 강함, 진입 검토 1순위 (frontrun 후보)",
    "WARM":            "🟢 WARM — 2축 강함, 추가 신호 모니터링",
    "LATE_SKEPTICAL":  "🌶 LATE_SKEPTICAL — 5%+ 공개됐으나 시장이 회의적 (역설적 매수 기회)",
    "WATCH":           "🟡 WATCH — 1축 매우 강함, 다른 축 보강 시 격상",
    "LATE_ACCESSIBLE": "🟧 LATE_ACCESSIBLE — 5%+ 공개, 시장 미온적 (잔여 정보 차익)",
    "LATE_PRICED_IN":  "✅ LATE_PRICED_IN — 5%+ 공개, 시장 이미 반영 (정보 우위 소진)",
    "LATE":            "⏰ LATE — 5%+ 공개, alpha 측정 불가 (filing 너무 최근)",
    "PASS":            "⚪ PASS — 현재 시점 강한 신호 없음",
    "AVOID":           "🔴 AVOID — 오너 60%+ 또는 명백한 록인",
}

# induty_code 앞 2자리 → sector 그룹 (보고서 표시용)
INDUTY_GROUPS = {
    "10": "음식료품", "11": "음식료품", "12": "음식료품",
    "13": "섬유의류", "14": "섬유의류",
    "15": "섬유의류", "16": "목재", "17": "펄프종이",
    "20": "화학", "21": "제약바이오", "22": "고무플라스틱",
    "23": "비금속광물", "24": "1차금속", "25": "금속가공",
    "26": "전자/IT", "27": "의료/광학", "28": "전기장비",
    "29": "기계", "30": "자동차", "31": "기타운송", "33": "기타제조",
    "35": "전기가스", "41": "건설", "42": "토목",
    "46": "도매", "47": "소매", "49": "운송", "51": "항공",
    "58": "출판", "59": "영상미디어", "62": "IT서비스", "63": "정보서비스",
    "64": "금융", "65": "보험", "68": "부동산",
    "70": "전문서비스", "73": "기타과학", "76": "리스/렌탈",
    "85": "교육", "86": "보건",
}


def induty_to_sector(code) -> str:
    if not code:
        return "기타"
    s = str(code)[:2] if len(str(code)) >= 2 else "??"
    return INDUTY_GROUPS.get(s, f"기타({s})")


# ─────────────────────────────────────────────────────────────────────────────
# Stock card
# ─────────────────────────────────────────────────────────────────────────────

def stock_card(r: dict, enr: dict) -> str:
    e = enr.get(r["ticker"], {})
    flow = e.get("flow") or {}
    co = e.get("company") or {}
    summary = e.get("summary") or {}
    th = e.get("treasury_disclosures") or []
    gov = e.get("governance_disclosures") or []
    mh = [x for x in (e.get("major_holdings_5pct") or []) if x.get("rcept_dt", "") >= "2025-01-01"]

    # treasury 최근 3건 (방향성 라벨 표시)
    treasury_str = ""
    if th:
        lines = [f"    - {x['rcept_dt']} `[{x.get('direction','?')}]` {x['report_nm']}"
                 for x in th[:3]]
        treasury_str = "\n  - **자사주 공시 (최근, 3건)**:\n" + "\n".join(lines)

    # governance 최근 3건
    gov_str = ""
    if gov:
        lines = [f"    - {x['rcept_dt']} `[{x.get('tag','?')}]` {x['report_nm']}"
                 for x in gov[:3]]
        gov_str = "\n  - **거버넌스/분할 공시 (최근, 3건)**:\n" + "\n".join(lines)

    # holders 최근 5건 (filer_type 표시)
    holders_str = ""
    if mh:
        lines = [
            f"    - {x['rcept_dt']} `[{x.get('filer_type','?')}]` {x.get('repror','?')} "
            f"({x.get('report_tp','')}, 지분 {x.get('stkrt','')}%, 변동 {x.get('stkrt_irds','')})"
            for x in mh[:5]
        ]
        holders_str = "\n  - **5%+ 보유공시 (2025년 이후, 최대 5건)**:\n" + "\n".join(lines)

    # 트리거된 룰 detail
    target_hits = "\n".join(f"    - {h}" for h in r.get("target_hits", []) or [])
    accum_hits = "\n".join(f"    - {h}" for h in r.get("accum_hits", []) or [])
    legal_hits = "\n".join(f"    - {h}" for h in r.get("legal_hits", []) or [])

    pf = r.get("post_filing") or {}
    pf_line = ""
    if pf:
        sec_alpha = pf.get("alpha_sector_pct")
        sec_str = f", sector α {sec_alpha:+.1f}%" if sec_alpha is not None else ""
        pf_line = (f"\n- **Post-filing**: stock {pf.get('stock_return_pct'):+.1f}% vs "
                   f"KOSPI {pf.get('kospi_return_pct'):+.1f}% → **α {pf.get('alpha_pct'):+.1f}%**{sec_str} "
                   f"(filing {pf.get('filing_date')}, D+{pf.get('days_since')})")

    # NAV (P3: trust score 추가)
    nav = r.get("nav") or {}
    nav_line = ""
    if nav and nav.get("listed_count", 0) > 0:
        trust = nav.get("trust", "?")
        trust_emoji = {"HIGH": "✅", "MEDIUM": "🟡", "LOW": "⚠️"}.get(trust, "")
        nav_line = (f"\n- **Sum-of-parts NAV**: 시총 {nav['parent_mcap_eok']:.0f}억 vs "
                    f"NAV {nav['total_nav_eok']:.0f}억 → 디스카운트 **{nav['discount_pct']:+.1f}%** "
                    f"(상장 {nav['listed_count']} + 비상장 {nav['unlisted_count']}, "
                    f"상장 NAV 비중 {nav.get('listed_share_pct',0):.0f}% — trust {trust_emoji} {trust})")

    # 일감몰아주기 — enriched에서 직접 가져옴 (scores.json 에는 raw 미저장)
    e = enr.get(r["ticker"], {})
    rpa = e.get("related_party_analysis") or {}
    rp_line = ""
    if rpa and rpa.get("ratio_pct") is not None:
        ratio = rpa.get("ratio_pct", 0)
        sales_eok = (rpa.get("related_party_sales_won") or 0) / 1e8
        total_eok = (rpa.get("total_revenue_won_yfinance")
                     or rpa.get("total_revenue_won") or 0) / 1e8
        tag = "🚨" if ratio >= 10 else ("⚠️" if ratio >= 5 else "")
        if ratio >= 1:
            rp_line = (f"\n- **일감몰아주기 비중**: {tag} {ratio:.1f}% "
                       f"(특수관계인 매출 {sales_eok:.0f}억 / 총매출 {total_eok:.0f}억) "
                       f"[{rpa.get('confidence','?')}]")

    # AVOID reason / 면제 사유
    ar = r.get("avoid_reason")
    ar_line = f"\n- **AVOID 룰**: {ar}" if ar else ""

    # sanity flags
    sanity = r.get("sanity_flags") or []
    sanity_line = "\n- " + " · ".join(sanity) if sanity else ""

    return f"""### {r['name']} ({r['ticker']}) — {r['tier']}

- **3축 점수**: T={r['target_score']} A={r['accum_score']} L={r['legal_score']} (총 {r['total_score']})
- **밸류**: PBR {r.get('PBR','?')} (sector median {r.get('sector_median_PBR','?')}) · 시총 {r.get('시가총액(억)','?')}억 · 최대주주 {r.get('최대주주_지분율(%)','?')}%
- **잉여자본비율**: {r.get('잉여자본비율','?')}
- **유동성**: capacity_score {r.get('capacity_score','?')}
- **업종**: {co.get('induty_code','?')} ({induty_to_sector(co.get('induty_code'))}) · 대표 {co.get('ceo_nm','?')}
- **기관플로우 90D**: {r.get('기관순매수_90D(억)','?')}억 · vs 매수VWAP {flow.get('vs_매수VWAP_90D(%)','?')}%
- **현재가**: {r.get('현재가','?')}원
- **최근 12M 행동주의 5%+ filing**: {r.get('recent_activist_12M', 0)}건{pf_line}{nav_line}{rp_line}{ar_line}{sanity_line}
- **Axis 1 (target attractiveness, T={r['target_score']})**:
{target_hits if target_hits else "    - (트리거 없음)"}
- **Axis 2 (accumulation signature, A={r['accum_score']})**:
{accum_hits if accum_hits else "    - (트리거 없음)"}
- **Axis 3 (legal vulnerability, L={r['legal_score']})**:
{legal_hits if legal_hits else "    - (트리거 없음)"}
- **참고 — LLM 정성 분류**: {r.get('primary_trap_type','?')} · {r.get('narrative','—')[:200]}
{treasury_str}{gov_str}{holders_str}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def build_summary(rows: list[dict], enr: dict, bt: dict, sector_medians: dict, kospi_median: float) -> str:
    s = ["## Executive Summary\n"]

    # AGM 타임라인
    agm = agm_context()
    phase_msg = {
        "stake_building": "🔵 **stake building** — 다음 주총까지 시간이 충분, 매집 시기.",
        "campaign_window": "🟠 **campaign window** — 주주제안 마감 임박.",
        "agm_window": "🟡 **AGM window** — 주총 직전.",
        "post_agm": "⚪ **post-AGM** — 캠페인 종료.",
    }
    s.append("### 🗓 정기주총 타임라인\n")
    s.append(f"- 오늘: {agm['today']}")
    s.append(f"- 다음 정기주총: **{agm['next_agm']}** (D-{agm['days_to_agm']})")
    s.append(f"- 주주제안 마감: **{agm['proposal_deadline']}** (D-{agm['days_to_deadline']})")
    s.append(f"- Phase: {phase_msg[agm['phase']]}")
    s.append("")

    # Tier 분포
    tier_cnt = Counter(r["tier"] for r in rows)
    s.append("### Tier 분포\n")
    s.append("| Tier | 개수 | 의미 |")
    s.append("|---|---|---|")
    for t in TIER_ORDER:
        cnt = tier_cnt.get(t, 0)
        s.append(f"| {t} | {cnt} | {TIER_HEADER.get(t,'').split(' — ')[1] if ' — ' in TIER_HEADER.get(t,'') else ''} |")
    s.append("")

    # ⚠️ regime change 경고
    s.append("### ⚠️ Regime Change Notice\n")
    s.append("> **상법 개정 (이사 충실의무 주주 포함)** 으로 행동주의 펀드 leverage 환경이 구조적으로 변화. "
             "Historical 백테스트 (2015~2025, n=205, 12M alpha **−3.8%**) 은 **구체제 데이터**이며 "
             "post-amendment 환경 예측 prior로 부적합. v5는 이를 calibration source 로 사용하지 않고, "
             "first-principles 룰베이스 3축 점수로 ranking.")
    s.append("")
    s.append(f"- **Historical reference only**: backtest_activist 12M alpha 평균 {(bt.get('summary',{}).get('alpha_12M',{}) or {}).get('mean','?')}% "
             f"(n={(bt.get('summary',{}).get('alpha_12M',{}) or {}).get('n','?')})")
    s.append(f"- **현재 KOSPI 전체 median PBR**: {kospi_median} (universe 비교 기준)")
    s.append("")

    # 우선 검토 종목 — frontrun 후보 + LATE_SKEPTICAL
    actionable_tiers = ("HOT", "WARM", "LATE_SKEPTICAL", "WATCH", "LATE_ACCESSIBLE")
    actionable = [r for r in rows if r["tier"] in actionable_tiers]
    s.append(f"### 우선 검토 종목 ({len(actionable)}개)\n")
    s.append("> 우선순위: HOT > WARM > **LATE_SKEPTICAL** > WATCH > LATE_ACCESSIBLE  \n"
             "> LATE_SKEPTICAL이 WATCH보다 위에 있는 이유: 5%+ 공개 이후 시장이 회의적이라 "
             "정보 우위가 *역설적으로 살아있는* 상태. 펀더멘털 강하면 catalyst 발현 시 큰 폭 상승.\n")
    if actionable:
        s.append("| Tier | 티커 | 종목 | T | A | L | total | PBR | 잉여자본 | filing 후 alpha | 12M |")
        s.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in actionable:
            pf = r.get("post_filing") or {}
            alpha_str = f"{pf.get('alpha_pct'):+.1f}% (D+{pf.get('days_since')})" if pf else "—"
            s.append(
                f"| **{r['tier']}** | {r['ticker']} | {r['name']} | "
                f"{r['target_score']} | {r['accum_score']} | {r['legal_score']} | "
                f"**{r['total_score']}** | {r.get('PBR','?')} | "
                f"{r.get('잉여자본비율','?')} | {alpha_str} | "
                f"{r.get('recent_activist_12M', 0)} |"
            )
    else:
        s.append("(현재 universe 에 actionable 종목 없음)")
    s.append("")

    # LATE 그룹 전체 요약
    late_all = [r for r in rows if r["tier"].startswith("LATE")]
    s.append(f"### LATE 그룹 전체 요약 ({len(late_all)}) — filing 후 alpha 분포\n")
    if late_all:
        s.append("| 티커 | 종목 | sub-tier | filing 후 stock | KOSPI 동기간 | **alpha** | days |")
        s.append("|---|---|---|---|---|---|---|")
        for r in sorted(late_all, key=lambda r: (r.get("post_filing") or {}).get("alpha_pct") or 0):
            pf = r.get("post_filing") or {}
            sr = pf.get("stock_return_pct")
            kr = pf.get("kospi_return_pct")
            ap = pf.get("alpha_pct")
            ds = pf.get("days_since")
            s.append(f"| {r['ticker']} | {r['name']} | {r['tier']} | "
                     f"{sr:+.1f}% | {kr:+.1f}% | **{ap:+.1f}%** | {ds} | "
                     if pf else
                     f"| {r['ticker']} | {r['name']} | {r['tier']} | — | — | — | — |")
    s.append("")

    # 섹터 집중도
    sectors = Counter()
    for r in rows:
        sec = induty_to_sector((enr.get(r["ticker"], {}).get("company") or {}).get("induty_code"))
        sectors[sec] += 1
    s.append("### 섹터 집중도 (전체 universe)\n")
    s.append("| 섹터 | 개수 | 비중 |")
    s.append("|---|---|---|")
    total = sum(sectors.values())
    for sec, cnt in sectors.most_common():
        s.append(f"| {sec} | {cnt} | {cnt/total*100:.0f}% |")
    s.append("")

    # DART 신호 통계
    n_with_activist = sum(1 for v in enr.values()
                          if v.get("summary", {}).get("recent_activist_filings_12M", 0) > 0)
    n_treasury_pos = sum(1 for v in enr.values()
                         if (v.get("summary", {}).get("treasury_score") or 0) > 0)
    n_split = sum(1 for v in enr.values()
                  if (v.get("summary", {}).get("governance_count") or {}).get("split_merge_24M", 0) > 0)
    n_incident = sum(1 for v in enr.values()
                     if (v.get("summary", {}).get("governance_count") or {}).get("incident_5Y", 0) > 0)
    s.append("### DART 신호 통계 (universe)\n")
    s.append(f"- 최근 12M 행동주의/PE 5%+ filing 있는 종목: **{n_with_activist}/{len(enr)}**")
    s.append(f"- 자사주 score > 0 (소각/매입 우세) 종목: **{n_treasury_pos}/{len(enr)}**")
    s.append(f"- 분할/합병 24M 공시 있는 종목: **{n_split}/{len(enr)}**")
    s.append(f"- 거버넌스 사고 5Y 이력 있는 종목: **{n_incident}/{len(enr)}**")
    s.append("")
    return "\n".join(s)


def build_categories(rows: list[dict], enr: dict) -> str:
    out = ["## Tier별 종목 분석\n"]
    by_tier: dict[str, list[dict]] = {}
    for r in rows:
        by_tier.setdefault(r["tier"], []).append(r)

    for tier in TIER_ORDER:
        sub = by_tier.get(tier, [])
        if not sub:
            continue
        out.append(f"## {TIER_HEADER[tier]} ({len(sub)}개)\n")
        for r in sub:
            out.append(stock_card(r, enr))
            out.append("---\n")
    return "\n".join(out)


METHODOLOGY_BRIEF = """
## 방법론 (요약)

### 1차 스크리닝 — `screen_value_ownership.py`
PBR ≤ 0.8 + 영업이익 3Q+ 흑자 + 매출 4Y 단조증가 + 최대주주 ≥ 40% + 잉여자본 컬럼

### 3축 점수 — `score_targets.py` (룰베이스, 가중치 `config.py`)

**Axis 1 — TARGET ATTRACTIVENESS** (행동주의 매력도)
- 잉여자본비율, 오너 sweet spot, PBR 갭, 시총 capacity, 자사주 정책

**Axis 2 — ACCUMULATION SIGNATURE** (매집 흔적)
- 90D 매수일 비율, 누적 net buy/시총, 매수VWAP 안정성, 20D 지속, capacity

**Axis 3 — LEGAL VULNERABILITY** (post-amendment 상법 leverage)
- 자사주 처분/신탁해지, 분할/합병, 거버넌스 사고, 임원 변동

**Tier 분류**: HOT (3축 ≥60) / WARM (2축 ≥60) / WATCH (1축 ≥70) / LATE (5%+ 이미) / PASS / AVOID (오너 ≥60%)

### 한계
- 3축 가중치는 도메인 지식 기반 — overfit 방지를 위해 historical fitting 안 함
- 일감몰아주기 (사업보고서 §X 파싱) 미구현, 추후 추가 예정
- 백테스트는 *historical reference only*, calibration prior 로 미사용
- LLM 분류 narrative는 sanity check 보조, 의사결정 1차 입력 X
"""


def main():
    data = json.load(open(SCORES_JSON, encoding="utf-8"))
    rows = data["rows"]
    sector_medians = data["sector_medians"]
    kospi_median = data["kospi_median_PBR"]
    enr = json.load(open(ENRICHED_JSON, encoding="utf-8"))
    bt = json.load(open(BACKTEST_ACTIVIST_JSON, encoding="utf-8")) if Path(BACKTEST_ACTIVIST_JSON).exists() else {}

    md = []
    md.append("# KOSPI 행동주의 후보 스크리닝 리포트 (v5 — 3축 룰베이스 ranking)\n")
    md.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    md.append(build_summary(rows, enr, bt, sector_medians, kospi_median))
    md.append(build_categories(rows, enr))
    md.append(METHODOLOGY_BRIEF)

    REPORT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"저장: {REPORT_MD} ({sum(len(s) for s in md)} chars)")


if __name__ == "__main__":
    main()
