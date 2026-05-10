"""CLI 진입점:

    python -m tools.filing_intel <rcept_no>                  # 단건
    python -m tools.filing_intel <rcept_no> --stock-code 016740   # 종목코드 명시
    python -m tools.filing_intel --self-test                 # 두올 케이스 자동 검증

출력:
    data/filing_intel/filing_intel_<rcept_no>.md
    data/filing_intel/filing_intel_index.json  (모든 분석 카탈로그)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# project root 의 src/ 와 data/ 를 사용
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from activist_scout.config import DATA_DIR  # noqa: E402

from . import classify, extract_llm, fetch_filing, grounding, report  # noqa: E402
from .resolve_filer import resolve  # noqa: E402


OUT_DIR = DATA_DIR / "filing_intel"


def _extract_stock_code_from_text(text: str) -> str | None:
    """본문에서 '회사코드 XXXXXX' 패턴 추출 (6자리 stock_code)."""
    m = re.search(r"회사코드\s*([0-9]{6})", text)
    return m.group(1) if m else None


def _stock_code_to_corp_code(stock_code: str) -> tuple[str | None, str | None]:
    """stock_code → (corp_code, corp_name)."""
    from activist_scout.config import CORP_MAP_FILE

    with open(CORP_MAP_FILE, encoding="utf-8") as f:
        cm = json.load(f)
    info = cm.get(stock_code)
    if not info:
        return None, None
    return info.get("corp_code"), info.get("corp_name")


def run_one(rcept_no: str, stock_code: str | None = None, *, do_grounding: bool = True) -> Path | None:
    """단일 5%+ 신고 → filing intel 보고서.

    Returns:
        저장된 보고서 경로, 또는 None (실패)
    """
    print(f"\n{'='*70}\n>>> filing_intel rcept_no={rcept_no}\n{'='*70}")

    # 1. document.xml 본문 다운로드
    print("[1/6] DART document.xml 다운로드 ...")
    text = fetch_filing.fetch_document_text(rcept_no)
    if not text:
        print(f"  ✗ 본문 추출 실패 (ZIP 아님 또는 빈 응답)")
        return None
    print(f"  ✓ 본문 {len(text):,} chars")

    # 2. 발행회사 코드 자동 추출 (stock_code 미지정 시)
    if not stock_code:
        stock_code = _extract_stock_code_from_text(text)
        print(f"  · 본문에서 stock_code = {stock_code}")
    corp_code, corp_name = _stock_code_to_corp_code(stock_code) if stock_code else (None, None)
    print(f"  · 발행회사 = {corp_name} (corp_code {corp_code})")

    # 3. majorstock 메타
    print("[2/6] majorstock 메타 조회 ...")
    meta = fetch_filing.fetch_majorstock_meta(rcept_no, corp_code) if corp_code else None
    file_date = ""
    if meta:
        file_date = meta.get("rcept_dt", "")
        print(f"  ✓ 신고일 {file_date}, 보고자 {meta.get('repror','?')}, 지분 {meta.get('stkrt','?')}%")
    else:
        print("  · majorstock 메타 미발견 (선택적 — 본문 추출로 대체)")

    # 4. LLM structured extract
    print("[3/6] Gemini structured output 으로 본문 → JSON ...")
    slices = fetch_filing.slice_around(text, fetch_filing.DEFAULT_KEYWORDS, window=600)
    extracted = extract_llm.extract_from_text(slices, full_head=text[:2000])
    if not extracted:
        print("  ✗ LLM 추출 실패")
        return None
    print(f"  ✓ 보유목적 = {extracted.get('보유목적')}, 보고구분 = {extracted.get('보고구분')}, "
          f"confidence = {extracted.get('confidence')}")

    # 5. 그룹 구조 역참조
    print("[4/6] 보고자 그룹 구조 역추적 ...")
    filer_name = extracted.get("보고자_명칭", "") or meta.get("repror", "") if meta else ""
    if not filer_name:
        print("  · 보고자 명칭 없음 — 추적 skip")
        filer_resolution: dict[str, Any] = {
            "filer_name": "",
            "match_method": "unresolved",
            "siblings": [],
        }
    else:
        filer_resolution = resolve(filer_name)
        print(f"  ✓ match_method = {filer_resolution.get('match_method')}, "
              f"parent = {filer_resolution.get('parent_corp_name')}")

    # 6. Google grounding
    grounding_text = ""
    grounding_sources: list[dict[str, str]] = []
    grounding_queries: list[str] = []
    if do_grounding and corp_name:
        print("[5/6] Gemini Google search grounding ...")
        gr = grounding.ground_filing(
            issuer_name=corp_name,
            issuer_ticker=stock_code or "",
            filer_name=filer_name,
            parent_listed=filer_resolution.get("parent_corp_name"),
            holding_purpose=extracted.get("보유목적", ""),
            file_date=file_date,
        )
        if gr:
            grounding_text = gr.text
            grounding_sources = gr.sources
            grounding_queries = gr.queries
            print(f"  ✓ {len(grounding_queries)} queries, {len(grounding_sources)} sources")
        else:
            print("  ✗ grounding 실패 — 보고서에서 §6 비워둠")
    else:
        print("[5/6] grounding skipped")

    # 7. 시나리오 분류
    print("[6/6] 시나리오 분류 + EV 분포 ...")
    classification = classify.classify(
        extracted=extracted,
        filer_resolution=filer_resolution,
        grounding_text=grounding_text,
    )
    if not classification:
        print("  ✗ classify 실패")
        return None
    print(f"  ✓ scenario = {classification.get('scenario')}, "
          f"EV mean = {classification.get('ev_mean_pct'):+.1f}%")

    # 8. 보고서 + 인덱스
    md = report.render(
        rcept_no=rcept_no,
        issuer_name=corp_name or "?",
        issuer_ticker=stock_code or "?",
        file_date=file_date,
        extracted=extracted,
        filer_resolution=filer_resolution,
        grounding_text=grounding_text,
        grounding_sources=grounding_sources,
        grounding_queries=grounding_queries,
        classification=classification,
    )
    path = report.save_report(md, OUT_DIR, rcept_no)
    report.save_index(OUT_DIR, rcept_no, {
        "issuer_name": corp_name,
        "issuer_ticker": stock_code,
        "file_date": file_date,
        "filer_name": filer_name,
        "scenario": classification.get("scenario"),
        "ev_mean_pct": classification.get("ev_mean_pct"),
        "confidence": classification.get("confidence"),
        "report_path": str(path.relative_to(DATA_DIR.parent)),
    })
    print(f"\n✅ 저장: {path}")
    return path


def self_test() -> bool:
    """두 케이스로 자동화 재현 검증:

    1) 프리미어 PE (rcept_no 20260504000081) — *동반 인수자* 시나리오
       - 보유목적 = "경영권 영향"
       - 시나리오 ≠ "행동주의캠페인" (PE_buyout / 산업통합M&A 가 적절)
       - 그룹 매핑은 unresolved 가 정답 (PE 는 모트렉스 자회사 아님)

    2) 모트렉스이에프엠 (rcept_no 20260430001599) — *주력 인수자* 시나리오
       - 보유목적 = "경영권 영향"
       - 그룹 매핑 = 모트렉스 (KOSDAQ 118990) — 자회사 역참조 PASS
       - siblings 에 시너지 후보 회사들 (한민내장, 성원매트 등)
    """
    print("\n" + "█" * 70)
    print("SELF-TEST 1: 프리미어 PE (rcept_no=20260504000081) — 동반 인수자 케이스")
    print("█" * 70)
    path1 = run_one("20260504000081", stock_code="016740", do_grounding=True)
    if not path1:
        print("\n❌ SELF-TEST 1 FAILED — run_one 반환 None")
        return False

    print("\n\n" + "█" * 70)
    print("SELF-TEST 2: 모트렉스이에프엠 (rcept_no=20260430001599) — 그룹 매핑 케이스")
    print("█" * 70)
    path2 = run_one("20260430001599", stock_code="016740", do_grounding=False)
    # grounding 은 1번만 — 비용·시간 절약
    if not path2:
        print("\n❌ SELF-TEST 2 FAILED — run_one 반환 None")
        return False

    # 인덱스 로드
    idx_path = OUT_DIR / "filing_intel_index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))

    print("\n\n" + "=" * 70)
    print("CHECKS")
    print("=" * 70)
    all_ok = True

    # 1. 프리미어 케이스 체크
    e1 = idx.get("20260504000081", {})
    md1 = path1.read_text(encoding="utf-8")
    c1 = [
        ("[1] 발행회사 = 두올", e1.get("issuer_name") == "두올"),
        ("[1] 종목코드 = 016740", e1.get("issuer_ticker") == "016740"),
        ("[1] 보고자에 '프리미어' 포함", "프리미어" in (e1.get("filer_name") or "")),
        ("[1] 시나리오는 행동주의캠페인 아님", e1.get("scenario") not in [None, "행동주의캠페인"]),
        ("[1] 본문에 '경영권 영향' 키워드", "경영권 영향" in md1),
    ]

    # 2. 모트렉스이에프엠 케이스 체크
    e2 = idx.get("20260430001599", {})
    md2 = path2.read_text(encoding="utf-8")
    c2 = [
        ("[2] 발행회사 = 두올", e2.get("issuer_name") == "두올"),
        ("[2] 보고자에 '모트렉스' 포함", "모트렉스" in (e2.get("filer_name") or "")),
        ("[2] 시나리오는 행동주의캠페인 아님", e2.get("scenario") not in [None, "행동주의캠페인"]),
        ("[2] 본문에 '경영권 영향' 키워드", "경영권 영향" in md2),
        ("[2] 그룹 매핑: '모트렉스' 모회사로 매칭", "모트렉스" in md2 and "상장 모회사" in md2),
    ]

    for name, ok in c1 + c2:
        mark = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  {mark} {name}")

    print("-" * 70)
    if all_ok:
        print("\n🎉 SELF-TEST PASSED — 두 케이스 모두 수동 분석 결과를 재현.")
    else:
        print("\n❌ SELF-TEST FAILED — 일부 체크 미통과. 위 ✗ 항목 점검.")
    return all_ok


def main():
    p = argparse.ArgumentParser(description="filing_intel — DART 5%+ 신고 분석")
    p.add_argument("rcept_no", nargs="?", help="DART 접수번호 (14자리)")
    p.add_argument("--stock-code", help="발행회사 종목코드 (생략 시 본문에서 자동 추출)")
    p.add_argument("--no-grounding", action="store_true", help="Google grounding 단계 skip")
    p.add_argument("--self-test", action="store_true", help="두올 케이스로 자동 검증")
    args = p.parse_args()

    if args.self_test:
        ok = self_test()
        sys.exit(0 if ok else 1)

    if not args.rcept_no:
        p.print_help()
        sys.exit(2)

    path = run_one(
        args.rcept_no,
        stock_code=args.stock_code,
        do_grounding=not args.no_grounding,
    )
    sys.exit(0 if path else 1)


if __name__ == "__main__":
    main()
