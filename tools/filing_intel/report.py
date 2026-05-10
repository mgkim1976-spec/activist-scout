"""Markdown 보고서 생성 — 한 5%+ 신고에 대한 종합 분석."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def render(
    *,
    rcept_no: str,
    issuer_name: str,
    issuer_ticker: str,
    file_date: str,
    extracted: dict[str, Any],
    filer_resolution: dict[str, Any],
    grounding_text: str,
    grounding_sources: list[dict[str, str]],
    grounding_queries: list[str],
    classification: dict[str, Any],
) -> str:
    """모든 단계 결과를 합쳐 단일 Markdown 문서로."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Header
    out: list[str] = []
    out.append(f"# Filing Intel — {issuer_name} ({issuer_ticker})")
    out.append("")
    out.append(f"*Generated: {now} · rcept_no `{rcept_no}` · 신고일 {file_date}*")
    out.append("")
    out.append(f"> ⚠️ **본 보고서는 자동 분석 *초안* 입니다.** LLM 환각 가능, "
               "원본 공시 본문 직접 검증 필수. CLAUDE.md §13 정신 유지.")
    out.append("")

    # §0 한 줄 요약
    summary = classification.get("summary_one_liner", "")
    scenario = classification.get("scenario", "?")
    ev_mean = classification.get("ev_mean_pct", 0)
    out.append("## §0. 한 줄 요약")
    out.append("")
    out.append(f"> **시나리오: {scenario}** — {summary}")
    out.append(f"> *12개월 EV 평균 추정: **{ev_mean:+.1f}%*** (분포 형태로 해석할 것)")
    out.append("")

    # §1 신고 메타
    out.append("## §1. 신고 메타 (DART 원문)")
    out.append("")
    fields = [
        ("발행회사", extracted.get("발행회사")),
        ("종목코드", extracted.get("발행회사_종목코드")),
        ("보고자", extracted.get("보고자_명칭")),
        ("보고자 구분", extracted.get("보고자_구분")),
        ("발행회사와의 관계", extracted.get("발행회사와의_관계")),
        ("보고구분", extracted.get("보고구분")),
        ("보유목적", extracted.get("보유목적")),
        ("보고사유", extracted.get("보고사유")),
        ("직전 보유주식수", extracted.get("직전_보유주식수")),
        ("이번 보유주식수", extracted.get("이번_보유주식수")),
        ("직전 보유비율 (%)", extracted.get("직전_보유비율_pct")),
        ("이번 보유비율 (%)", extracted.get("이번_보유비율_pct")),
        ("취득금액 (원)", extracted.get("취득금액_원")),
        ("취득자금 자기자금 (원)", extracted.get("취득자금_자기자금_원")),
        ("취득자금 차입금 (원)", extracted.get("취득자금_차입금_원")),
        ("취득자금 기타 (원)", extracted.get("취득자금_기타_원")),
        ("차입처", extracted.get("차입처")),
        ("거래종결 조건", extracted.get("거래종결_조건")),
        ("취득자금 조성 경위", extracted.get("취득자금_조성_경위")),
    ]
    out.append("| 항목 | 값 |")
    out.append("|---|---|")
    for k, v in fields:
        if v is None or v == "" or v == 0:
            continue
        if isinstance(v, int) and v > 1e8:
            v = f"{v:,} ({v/1e8:.1f}억)"
        out.append(f"| {k} | {v} |")
    out.append("")

    specials = extracted.get("특별관계자_명단") or []
    if specials:
        out.append("**특별관계자 명단:**")
        for s in specials:
            out.append(f"- {s}")
        out.append("")

    evidence = extracted.get("evidence", "")
    if evidence:
        out.append(f"**evidence (LLM 추출 근거 본문 인용):** *{evidence}*")
        out.append("")

    confidence = extracted.get("confidence", "?")
    out.append(f"**Extraction confidence: `{confidence}`**")
    out.append("")

    # §2 보고자 그룹 구조
    out.append("## §2. 보고자 그룹 구조 (자회사 역참조)")
    out.append("")
    method = filer_resolution.get("match_method", "?")
    parent = filer_resolution.get("parent_corp_name")
    parent_stock = filer_resolution.get("parent_stock_code")
    if method == "direct_listed":
        out.append(f"보고자 = *상장사 본인* ({parent}, 종목코드 {parent_stock})")
    elif method == "subsidiary_reverse_lookup":
        out.append(
            f"**비상장 보고자 → 상장 모회사 발견:** "
            f"`{filer_resolution['filer_name']}` → **{parent}** "
            f"(KOSPI/KOSDAQ {parent_stock})"
        )
        ev_inv = filer_resolution.get("evidence_inv_prm", "")
        if ev_inv:
            out.append(f"")
            out.append(f"- *자회사 매칭 evidence*: `{ev_inv}` (모회사의 사업보고서 §VIII)")
        siblings = filer_resolution.get("siblings") or []
        if siblings:
            out.append(f"")
            out.append(f"**같은 모회사 산하 다른 자회사 (시너지 후보):**")
            for s in siblings[:20]:
                out.append(f"- {s}")
    else:
        out.append(f"⚠️ **상장 모회사 추적 실패** — 보고자 `{filer_resolution.get('filer_name')}` "
                   "는 비상장이며 자회사 역참조에서도 매칭 안 됨. 사람 검증 필요.")
    out.append("")

    # §3 시나리오 분류
    out.append("## §3. 시나리오 분류")
    out.append("")
    out.append(f"**시나리오 = `{scenario}`**")
    out.append("")
    reasoning = classification.get("scenario_reasoning", "")
    if reasoning:
        out.append(reasoning)
        out.append("")

    # §4 EV 분포
    out.append("## §4. 12개월 EV 분포 (시스템 초안)")
    out.append("")
    out.append("| 시나리오 | 확률 (%) | 가격 영향 (%) | EV 기여 (%) |")
    out.append("|---|---:|---:|---:|")
    dist = classification.get("ev_distribution_12m", [])
    total_contrib = 0.0
    for d in dist:
        p = d.get("probability_pct", 0)
        i = d.get("price_impact_pct", 0)
        contrib = p * i / 100
        total_contrib += contrib
        out.append(f"| {d.get('label','')} | {p:.0f} | {i:+.1f} | {contrib:+.2f} |")
    out.append(f"| **합계 (가중 평균)** | **100** | — | **{ev_mean:+.2f}** |")
    out.append("")
    window = classification.get("catalyst_window_days")
    if window:
        out.append(f"**catalyst window: D-{window}**")
        out.append("")

    # §5 권장 사이즈
    out.append("## §5. 권장 진입 사이즈 (초안)")
    out.append("")
    indiv = classification.get("recommended_size_individual", "?")
    fund = classification.get("recommended_size_fund", "?")
    out.append(f"- **개인 투자자**: {indiv}")
    out.append(f"- **펀드 매니저**: {fund}")
    out.append("")

    # §6 grounding
    out.append("## §6. 언론·시장 사실 (Google grounding)")
    out.append("")
    if grounding_text:
        out.append(grounding_text)
        out.append("")
    if grounding_queries:
        out.append("**Search queries used:**")
        for q in grounding_queries:
            out.append(f"- `{q}`")
        out.append("")
    if grounding_sources:
        out.append("**출처 (사용자 검증용):**")
        for i, s in enumerate(grounding_sources, 1):
            out.append(f"{i}. [{s.get('title','(no title)')}]({s.get('uri','')})")
        out.append("")

    # §7 사람 검증
    out.append("## §7. ⚠️ 사람 검증 필수 항목 (자동화 외 PM 영역)")
    out.append("")
    ppl = classification.get("people_verification_required", [])
    for p in ppl:
        out.append(f"- {p}")
    out.append("")
    out.append("> **공통 사람 검증 영역** (모든 5%+ 신고에 적용):")
    out.append("> - 인수자/PE 의 *진짜 의도* (위장 행동주의 / 단순 시너지 / 청산)")
    out.append("> - 매니지먼트 인터뷰 / 산업 가십 / 경쟁사 동향")
    out.append("> - 정부·규제 승인 일정 (공정거래위원회 등)")
    out.append("> - 외국계 자금 진입 신호 (Bloomberg / 헤드헌터 네트워크)")
    out.append("")

    # Footer
    out.append("---")
    out.append("")
    out.append("*본 보고서는 자동 분석 초안이며 투자 권유가 아닙니다. "
               "최종 의사결정은 사람이 합니다. DISCLAIMER.md 참조.*")

    return "\n".join(out)


def save_report(content: str, out_dir: Path, rcept_no: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"filing_intel_{rcept_no}.md"
    path.write_text(content, encoding="utf-8")
    return path


def save_index(out_dir: Path, rcept_no: str, summary: dict[str, Any]) -> Path:
    """전체 보고서 인덱스 (filing_intel_index.json) 유지."""
    out_dir.mkdir(parents=True, exist_ok=True)
    idx_path = out_dir / "filing_intel_index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    else:
        idx = {}
    idx[rcept_no] = summary
    idx_path.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return idx_path
