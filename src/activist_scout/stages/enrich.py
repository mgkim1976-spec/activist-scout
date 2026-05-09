"""
스크리닝 후보에 DART 공시 보강 + 도메인 분류.

수집(직전 ~13개월):
- 자사주 공시 → domain.classify_treasury로 방향성 카테고리 라벨
- 5%+ 대량보유공시 → domain.classify_filer로 보고자 유형 라벨
- 임원·주요주주 소유 변동
- 회사개요

요약 메트릭 (LLM과 리포트 모두 사용):
- treasury_score: 자사주 공시 방향성 가중치 합 (양수=우호적)
- filer_summary: 유형별 카운트 + 행동주의 신규 진입 여부
"""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd

from activist_scout.config import ENRICHED_JSON, FLOW_CSV, HOLDINGS_CUTOFF_DATE, LIQUIDITY_CSV, SCREENING_CSV, require
from activist_scout.domain import classify_filer, classify_governance_disclosure, classify_treasury, treasury_score
from activist_scout.utils import dart_get, load_corp_map


def fetch_company_overview(corp_code: str) -> dict | None:
    j = dart_get("company.json", {"corp_code": corp_code})
    if not j or j.get("status") != "000":
        return None
    keep = ("corp_name", "stock_name", "induty_code", "ceo_nm",
            "est_dt", "acc_mt", "adres", "hm_url")
    return {k: j.get(k) for k in keep}


def fetch_disclosures(corp_code: str, bgn: str, end: str) -> tuple[list[dict], list[dict]]:
    """단일 list.json 순회로 자사주 + 거버넌스/분할 공시를 모두 수집.

    Returns:
        (treasury, governance) — 각각 raw + 분류 라벨 부착
    """
    treasury: list[dict] = []
    governance: list[dict] = []
    page = 1
    while page <= 5:
        j = dart_get(
            "list.json",
            {"corp_code": corp_code, "bgn_de": bgn, "end_de": end,
             "page_no": page, "page_count": 100},
        )
        if not j or j.get("status") != "000":
            break
        for it in j.get("list", []):
            nm = it.get("report_nm", "") or ""
            base = {
                "rcept_dt": it.get("rcept_dt"),
                "report_nm": nm,
                "rcept_no": it.get("rcept_no"),
                "flr_nm": it.get("flr_nm"),
            }
            # 자사주
            if "자기주식" in nm or "자사주" in nm:
                treasury.append({**base, "direction": classify_treasury(nm)})
            # 거버넌스/분할 (자사주와 분리)
            tag = classify_governance_disclosure(nm)
            if tag != "other":
                governance.append({**base, "tag": tag})
        if int(j.get("page_no", 1)) >= int(j.get("total_page", 1)):
            break
        page += 1
    return treasury, governance


def fetch_major_holdings(corp_code: str) -> list[dict]:
    """5%+ 대량보유 + 보고자 유형 라벨."""
    j = dart_get("majorstock.json", {"corp_code": corp_code})
    if not j or j.get("status") != "000":
        return []
    keep = ("rcept_no", "rcept_dt", "repror", "report_tp", "stkrt", "stkrt_irds",
            "ctr_stkrt", "report_resn")
    out = []
    for it in j.get("list", []):
        d = {k: it.get(k) for k in keep}
        d["filer_type"] = classify_filer(d.get("repror", ""))
        out.append(d)
    return out


def fetch_executive_holdings(corp_code: str) -> list[dict]:
    j = dart_get("elestock.json", {"corp_code": corp_code})
    if not j or j.get("status") != "000":
        return []
    keep = ("rcept_dt", "repror", "isu_exctv_rgist_at", "isu_exctv_ofcps",
            "sp_stock_lmp_cnt", "sp_stock_lmp_irds_cnt", "sp_stock_lmp_irds_rate")
    return [{k: it.get(k) for k in keep} for it in j.get("list", [])][:30]


def fetch_executive_tenure(corp_code: str, year: int) -> list[dict]:
    """임원 현황 (사업보고서 §VI) — 사외이사 임기 만료일.

    Returns 임원 list with parsed tenure_end_on (date).
    """
    import re
    j = dart_get(
        "exctvSttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    if not j or j.get("status") != "000":
        return []
    out = []
    for it in j.get("list", []):
        end_str = (it.get("tenure_end_on") or "").strip()
        # "2027년 03월 26일" → "2027-03-26"
        m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", end_str)
        end_iso = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
        out.append({
            "name":            it.get("nm"),
            "ofcps":           it.get("ofcps"),
            "rgist_at":        it.get("rgist_exctv_at"),       # "사내이사", "사외이사", "감사"
            "fte_at":          it.get("fte_at"),
            "chrg_job":        it.get("chrg_job"),
            "tenure_end":      end_iso,
            "tenure_end_raw":  end_str,
            "hffc_pd_months":  it.get("hffc_pd"),
        })
    return out


def _to_float(s: str | None) -> float:
    """DART 문자열 (콤마 포함) → float. 실패 시 0."""
    if s is None:
        return 0.0
    s = str(s).replace(",", "").strip()
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_subsidiaries(corp_code: str, year: int) -> list[dict]:
    """타법인 출자 현황 (사업보고서 §VIII) — 자회사·관계사 정보.

    Sum-of-parts NAV 계산용. 자회사가 상장사면 시총 매핑 가능.
    """
    j = dart_get(
        "otrCprInvstmntSttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    if not j or j.get("status") != "000":
        return []
    out = []
    for it in j.get("list", []):
        out.append({
            "name":            it.get("inv_prm"),
            "purpose":         it.get("invstmnt_purps"),
            "stake_pct":       _to_float(it.get("trmend_blce_qota_rt")),
            "book_value_won":  _to_float(it.get("trmend_blce_acntbk_amount")),
            "subsidiary_total_assets": _to_float(it.get("recent_bsns_year_fnnr_sttus_tot_assets")),
            "subsidiary_net_income": _to_float(it.get("recent_bsns_year_fnnr_sttus_thstrm_ntpf")),
        })
    return out


def fetch_business_report_rcept_no(corp_code: str, year: int) -> str | None:
    """주어진 연도의 사업보고서 rcept_no. 사업보고서는 다음 해 3월 제출."""
    bgn = f"{year + 1}0101"
    end = f"{year + 1}0501"
    j = dart_get(
        "list.json",
        {"corp_code": corp_code, "bgn_de": bgn, "end_de": end,
         "pblntf_detail_ty": "A001", "page_count": 10},
    )
    if not j or j.get("status") != "000":
        return None
    for it in j.get("list", []):
        nm = it.get("report_nm", "") or ""
        if "사업보고서" in nm and "기재" not in nm:
            return it.get("rcept_no")
    return None


def fetch_related_party_section(rcept_no: str, max_chars: int = 12000) -> str | None:
    """사업보고서 본문에서 '특수관계자와의 거래' 섹션 텍스트 추출.

    1) /api/document.xml → ZIP 다운로드
    2) 가장 큰 XML 파일이 본문
    3) '특수관계자와의 거래' 또는 '특수관계자거래' 키워드 위치부터 max_chars 추출
    4) HTML/XML 태그 제거 + 공백 정리
    """
    import io, zipfile, re
    from activist_scout.utils import _DART_SESSION
    from activist_scout.config import DART_API_KEY

    try:
        r = _DART_SESSION.get(
            "https://opendart.fss.or.kr/api/document.xml",
            params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no},
            timeout=60,
        )
        if r.status_code != 200 or r.content[:2] != b"PK":
            return None
        z = zipfile.ZipFile(io.BytesIO(r.content))
        files = sorted(z.namelist(), key=lambda n: -z.getinfo(n).file_size)
        if not files:
            return None
        text = z.read(files[0]).decode("utf-8", errors="replace")
    except Exception:
        return None

    # 키워드 우선순위로 위치 탐색
    for kw in ("특수관계자와의 거래", "특수관계자 거래", "특수관계자에 대한"):
        idx = text.find(kw)
        if idx > 0:
            break
    else:
        return None

    chunk = text[idx:idx + max_chars]
    # HTML/XML 태그 제거 + 다중 공백 → 단일 공백
    chunk = re.sub(r"<[^>]+>", " ", chunk)
    chunk = re.sub(r"&nbsp;|&amp;|&lt;|&gt;", " ", chunk)
    chunk = re.sub(r"\s+", " ", chunk)
    return chunk.strip()[:max_chars]


def summarize(treasury: list[dict], governance: list[dict], holdings: list[dict]) -> dict:
    """LLM/리포트/score_targets 가 사용할 요약 메트릭."""
    # 자사주
    dirs = [t["direction"] for t in treasury]
    treasury_dir_count = dict(Counter(dirs))
    score = treasury_score(dirs)

    # 거버넌스/분할 — 직전 24M
    cutoff_24m = (datetime.now() - timedelta(days=24 * 30)).strftime("%Y-%m-%d")
    cutoff_5y = (datetime.now() - timedelta(days=5 * 365)).strftime("%Y-%m-%d")
    gov_24m = [g for g in governance if g.get("rcept_dt", "") >= cutoff_24m]
    incident_5y = [g for g in governance if g.get("tag") == "incident"
                   and g.get("rcept_dt", "") >= cutoff_5y]
    split_24m = [g for g in gov_24m if g.get("tag") == "split_merge"]
    governance_count = {
        "split_merge_24M": len(split_24m),
        "incident_5Y": len(incident_5y),
    }

    # 5%+ 보고자
    filer_count = Counter(h["filer_type"] for h in holdings)
    cutoff_12m = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    recent_activists = [
        h for h in holdings
        if h["filer_type"] in ("activist", "semi_activist", "pe_fund")
        and h.get("rcept_dt", "") >= cutoff_12m
    ]
    return {
        "treasury_dir_count": treasury_dir_count,
        "treasury_score": round(score, 2),
        "governance_count": governance_count,
        "filer_count": dict(filer_count),
        "recent_activist_filings_12M": len(recent_activists),
        "recent_activists": [
            {"date": h.get("rcept_dt"), "name": h.get("repror"),
             "type": h["filer_type"], "stkrt": h.get("stkrt"),
             "irds": h.get("stkrt_irds")}
            for h in recent_activists[:5]
        ],
    }


def enrich_one(ticker: str, name: str, corp_code: str, year: int,
               fetch_report_text: bool = False) -> dict:
    end = datetime.now().strftime("%Y%m%d")
    # 거버넌스 사고는 최대 5년 lookback
    bgn = (datetime.now() - timedelta(days=5 * 365)).strftime("%Y%m%d")
    treasury, governance = fetch_disclosures(corp_code, bgn, end)
    holdings = fetch_major_holdings(corp_code)
    out = {
        "ticker": ticker,
        "name": name,
        "corp_code": corp_code,
        "company": fetch_company_overview(corp_code),
        "treasury_disclosures": treasury,
        "governance_disclosures": governance,
        "major_holdings_5pct": holdings,
        "exec_holdings": fetch_executive_holdings(corp_code),
        "exec_tenure": fetch_executive_tenure(corp_code, year),    # P1 v7: catalyst timing
        "subsidiaries": fetch_subsidiaries(corp_code, year),
        "summary": summarize(treasury, governance, holdings),
    }
    if fetch_report_text:
        rcept_no = fetch_business_report_rcept_no(corp_code, year)
        if rcept_no:
            out["business_report_rcept_no"] = rcept_no
            out["related_party_section"] = fetch_related_party_section(rcept_no)
    return out


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.now().year - 1,
                        help="DART 사업보고서 기준 연도 (default: 작년)")
    parser.add_argument("--report-text", action="store_true",
                        help="사업보고서 본문 §X (특수관계자 거래) 텍스트도 추출 (느림 ~5분)")
    args = parser.parse_args()

    require("DART_API_KEY")
    corp_map = load_corp_map()
    df = pd.read_csv(SCREENING_CSV, dtype={"ticker": str})
    flow = pd.read_csv(FLOW_CSV, dtype={"ticker": str}).set_index("ticker").to_dict("index")
    liq = {}
    if LIQUIDITY_CSV.exists():
        liq = pd.read_csv(LIQUIDITY_CSV, dtype={"ticker": str}).set_index("ticker").to_dict("index")

    print(f"DART 보강 대상: {len(df)}개 (사업보고서 기준 연도: {args.year})")
    enriched = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for _, r in df.iterrows():
            cm = corp_map.get(r["ticker"])
            if not cm:
                continue
            futs[ex.submit(enrich_one, r["ticker"], r["name"],
                           cm["corp_code"], args.year,
                           args.report_text)] = r["ticker"]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                d = fut.result()
                tk = d["ticker"]
                row = df[df["ticker"] == tk].iloc[0].to_dict()
                d["fundamentals"] = {
                    k: row.get(k) for k in
                    ["PBR", "PER", "시가총액(억)", "최대주주_지분율(%)",
                     "잉여자본비율", "순현금(억)",
                     "영업이익_3Q(백만원)", "매출_4Y(억원)"]
                }
                d["flow"] = flow.get(tk, {})
                d["liquidity"] = liq.get(tk, {})
                enriched[tk] = d
            except Exception as e:
                print(f"  ! 실패 {futs[fut]}: {e}")
            if i % 10 == 0:
                print(f"  ... {i}/{len(futs)}")

    with open(ENRICHED_JSON, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)
    print(f"저장: {ENRICHED_JSON} ({len(enriched)}개)")
    print(f"  - 5%+ 보유공시 있는 종목: {sum(1 for v in enriched.values() if v.get('major_holdings_5pct'))}개")
    print(f"  - 자사주 공시 있는 종목: {sum(1 for v in enriched.values() if v.get('treasury_disclosures'))}개")
    print(f"  - 분할/합병 공시 24M 있는 종목: "
          f"{sum(1 for v in enriched.values() if v['summary']['governance_count']['split_merge_24M'] > 0)}개")
    print(f"  - 거버넌스 사고 5Y 있는 종목: "
          f"{sum(1 for v in enriched.values() if v['summary']['governance_count']['incident_5Y'] > 0)}개")
    print(f"  - 최근 12M 행동주의 신규/변동 filing: "
          f"{sum(1 for v in enriched.values() if v['summary']['recent_activist_filings_12M'] > 0)}개")
    print(f"  - 자사주 score > 0 (우호적): "
          f"{sum(1 for v in enriched.values() if v['summary']['treasury_score'] > 0)}개")


if __name__ == "__main__":
    main()
