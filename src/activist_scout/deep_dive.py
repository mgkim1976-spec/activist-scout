"""
선택한 종목에 대해 PM 레벨 deep dive 보고서를 자동 생성.

사용법:
  python deep_dive.py 021820                    # 단일 종목
  python deep_dive.py 021820 --output deep_dive_021820.md
  python deep_dive.py --ticker 021820 --print   # stdout 출력

데이터 흐름:
  1) enriched.json + scores.json 에서 기존 데이터 로드
  2) 누락 데이터 보강 — DART hyslrSttus, 자기주식 보유, 최근 12M 공시
  3) acc_mt 기반 catalyst calendar 자동 산출
  4) OpenAI (gpt-5.4-mini default) 호출 — structured deep dive narrative
  5) Markdown 12-section 보고서 출력

LLM 모델: config.OPENAI_MODEL (.env 의 OPENAI_MODEL 로 변경 가능)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from openai import OpenAI

from activist_scout.config import (
    DART_VIEWER_URL, ENRICHED_JSON, OPENAI_API_KEY, OPENAI_MODEL,
    SCORES_JSON, require,
)
from activist_scout.utils import dart_get


# ─────────────────────────────────────────────────────────────────────────────
# 1) 데이터 수집 (기존 + 보강)
# ─────────────────────────────────────────────────────────────────────────────

def load_existing(ticker: str) -> tuple[dict, dict]:
    enr_all = json.load(open(ENRICHED_JSON, encoding="utf-8"))
    sc_all = json.load(open(SCORES_JSON, encoding="utf-8"))
    e = enr_all.get(ticker)
    if not e:
        raise SystemExit(f"{ticker} 가 enriched.json 에 없음. screening 파이프라인 먼저 실행하세요.")
    s = next((r for r in sc_all["rows"] if r["ticker"] == ticker), None)
    if not s:
        raise SystemExit(f"{ticker} 가 scores.json 에 없음.")
    return e, s


def fetch_shareholder_detail(corp_code: str, year: int) -> list[dict]:
    """hyslrSttus.json 의 보통주 상세 명단."""
    j = dart_get(
        "hyslrSttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    if not j or j.get("status") != "000":
        return []
    out = []
    for it in j.get("list", []):
        if it.get("stock_knd") != "보통주":
            continue
        out.append({
            "name": it.get("nm"),
            "relate": it.get("relate"),
            "shares": it.get("trmend_posesn_stock_co"),
            "stake_pct": it.get("trmend_posesn_stock_qota_rt"),
        })
    return out


def fetch_recent_disclosures(corp_code: str, days: int = 365) -> list[dict]:
    """최근 N일 모든 공시 — 일정·캠페인·특수 사건 추적."""
    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    out = []
    page = 1
    while page <= 5:
        j = dart_get(
            "list.json",
            {"corp_code": corp_code, "bgn_de": bgn, "end_de": end,
             "page_no": page, "page_count": 100},
        )
        if not j or j.get("status") != "000":
            break
        out.extend(j.get("list", []))
        if int(j.get("page_no", 1)) >= int(j.get("total_page", 1)):
            break
        page += 1
    # 핵심 필드만 추리고 50건 cap
    keep = ("rcept_dt", "report_nm", "rcept_no", "flr_nm")
    return [{k: it.get(k) for k in keep} for it in out[:50]]


def fetch_treasury_holdings(corp_code: str, year: int) -> list[dict]:
    """자기주식 취득·처분·소각 현황 (사업보고서 §VII)."""
    j = dart_get(
        "tesstkAcqsDspsSttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    if not j or j.get("status") != "000":
        return []
    return j.get("list", [])[:10]


# ─────────────────────────────────────────────────────────────────────────────
# 1.5) 추가 helper (v2 — manual 능가용)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_quarterly_financials(yf_symbol: str, n: int = 6) -> dict:
    """yfinance 분기 financials — 매출/영업이익/순이익 + 합계 + OPM.

    *사업보고서 4Y 매출(연결 누계)과 분기 trend 해석을 분리*하기 위해 분기 데이터 별도 제공.
    """
    out = {"quarters": [], "fy_estimate": {}}
    try:
        qf = yf.Ticker(yf_symbol).quarterly_financials
        if qf is None or qf.empty:
            return out
        cols = sorted(qf.columns, reverse=True)[:n]
        keys = ("Total Revenue", "Operating Income", "Net Income")
        for c in cols:
            row = {"quarter": c.strftime("%Y-%m")}
            for k in keys:
                if k in qf.index and qf.loc[k, c] is not None:
                    row[k.lower().replace(" ", "_")] = round(float(qf.loc[k, c]) / 1e8, 1)
            out["quarters"].append(row)
        # 최근 4 분기 합산 (가장 최근 회계연도 추정)
        last4 = out["quarters"][:4]
        if len(last4) == 4:
            rev = sum(q.get("total_revenue") or 0 for q in last4)
            oi = sum(q.get("operating_income") or 0 for q in last4)
            ni = sum(q.get("net_income") or 0 for q in last4)
            out["fy_estimate"] = {
                "revenue_eok": round(rev, 1),
                "operating_income_eok": round(oi, 1),
                "net_income_eok": round(ni, 1),
                "opm_pct": round(oi / rev * 100, 1) if rev > 0 else None,
                "qoq_revenue": [q.get("total_revenue") for q in last4],
            }
    except Exception:
        pass
    return out


def fetch_balance_sheet(yf_symbol: str) -> dict:
    """yfinance 대차대조표 — 현금/투자/부채/자본 명시 (manual 와 동일).

    부채 NaN 시 DART 재무제표 fallback 가능 (현재는 yfinance 우선).
    """
    out = {}
    try:
        bs = yf.Ticker(yf_symbol).balance_sheet
        if bs is None or bs.empty:
            return out
        col = sorted(bs.columns, reverse=True)[0]
        keys = {
            "cash": "Cash And Cash Equivalents",
            "st_inv": "Other Short Term Investments",
            "total_debt": "Total Debt",
            "current_debt": "Current Debt",
            "long_term_debt": "Long Term Debt",
            "stockholders_equity": "Stockholders Equity",
            "total_assets": "Total Assets",
            "total_liabilities": "Total Liabilities Net Minority Interest",
        }
        for k, src in keys.items():
            if src in bs.index:
                v = bs.loc[src, col]
                if v is not None and not (isinstance(v, float) and v != v):  # not NaN
                    out[k + "_eok"] = round(float(v) / 1e8, 0)
        out["as_of"] = str(col)[:10]
    except Exception:
        pass
    return out


def analyze_5pct_trend(holdings: list[dict]) -> dict:
    """5%+ 보고 시계열 분석 — 외부/strategic 분리, 매수/매도 추세."""
    by_filer: dict[str, list[dict]] = {}
    for h in holdings:
        repror = h.get("repror") or "?"
        by_filer.setdefault(repror, []).append(h)

    series = []
    for repror, lst in by_filer.items():
        sorted_lst = sorted(lst, key=lambda x: x.get("rcept_dt", ""))
        latest = sorted_lst[-1]
        first = sorted_lst[0]
        # 변동 합산
        try:
            net_change = sum(float(h.get("stkrt_irds") or 0) for h in lst)
        except (TypeError, ValueError):
            net_change = 0
        series.append({
            "repror": repror,
            "filer_type": latest.get("filer_type"),
            "first_date": first.get("rcept_dt"),
            "latest_date": latest.get("rcept_dt"),
            "latest_stake": latest.get("stkrt"),
            "net_change_pct": round(net_change, 2),
            "n_filings": len(lst),
            "trend": "매수" if net_change > 0.5 else ("매도" if net_change < -0.5 else "중립"),
        })

    series.sort(key=lambda x: -float(x.get("latest_stake") or 0))

    # 외부 (strategic/individual 제외) trend 요약
    external = [s for s in series if s["filer_type"] not in ("strategic",)
                and (float(s.get("latest_stake") or 0) >= 5.0)]
    summary = {
        "total_unique_filers": len(series),
        "external_5pct_count": len(external),
        "external_5pct_selling": sum(1 for s in external if s["trend"] == "매도"),
        "external_5pct_buying": sum(1 for s in external if s["trend"] == "매수"),
    }
    return {"per_filer": series, "summary": summary, "external_5pct": external}


def compute_implied_nav(subsidiaries: list[dict]) -> dict:
    """자회사 자산 × 보유지분 = implied 자회사 가치 (장부가 vs 이론가 비교).

    PM 관점: 비상장 자회사의 *장부가는 원시 취득가* — 자산의 일부에 불과.
    자산 × 지분이 더 합리적 lower bound (단, 부채 차감 미반영 — 추정 가치).
    """
    rows = []
    book_total = 0.0
    asset_implied = 0.0
    for s in subsidiaries:
        if (s.get("name") or "").strip() == "합계":
            continue
        stake = (s.get("stake_pct") or 0) / 100
        book = (s.get("book_value_won") or 0) / 1e8
        asset = (s.get("subsidiary_total_assets") or 0) / 1e8
        ni = (s.get("subsidiary_net_income") or 0) / 1e8
        implied = asset * stake     # 자산 × 지분 (rough upper bound — 부채 미차감)
        if asset > 0 or book > 0:
            rows.append({
                "name": s.get("name"),
                "stake_pct": s.get("stake_pct"),
                "book_eok": round(book, 1),
                "asset_eok": round(asset, 1),
                "ni_eok": round(ni, 1),
                "implied_eok": round(implied, 1),
                "implied_vs_book_x": round(implied / book, 2) if book > 0 else None,
            })
            book_total += book
            asset_implied += implied
    rows.sort(key=lambda r: -(r.get("implied_eok") or 0))
    return {
        "rows": rows,
        "book_total_eok": round(book_total, 0),
        "asset_implied_total_eok": round(asset_implied, 0),
        "uplift_x": round(asset_implied / book_total, 2) if book_total > 0 else None,
    }


def compute_market_impact(adv_eok: float | None, mcap_eok: float | None,
                         target_pct: float = 1.5) -> dict:
    """진입 사이즈 + 시장임팩트 정량.

    target_pct (% of portfolio = % of mcap of position): 매집 capacity 분석.
    """
    if not adv_eok or not mcap_eok:
        return {}
    target_eok = mcap_eok * target_pct / 100
    daily_buy_eok = adv_eok * 0.05
    days_to_fill = target_eok / daily_buy_eok if daily_buy_eok > 0 else float("inf")
    return {
        "target_pct": target_pct,
        "target_eok": round(target_eok, 1),
        "adv_eok": round(adv_eok, 2),
        "daily_buy_5pct_adv_eok": round(daily_buy_eok, 2),
        "estimated_days_to_fill": round(days_to_fill, 1),
        "weeks_to_fill": round(days_to_fill / 5, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# v4 추가 helper — significant filings + industry context + PnL impact
# ─────────────────────────────────────────────────────────────────────────────

# 큰 재무 임팩트 / 거버넌스 의미 가능성 있는 공시 키워드
SIGNIFICANT_FILING_KEYWORDS = (
    "채무보증",          # 자회사 보증 → 자본 유출 가능
    "타인에대한",        # 타인 채무/지급 보증
    "유형자산처분",      # 부동산/공장 매각
    "유형자산취득",
    "타법인주식",        # 자회사·계열사 지분 변동
    "출자",              # 신규 출자
    "분할",              # 인적/물적 분할
    "합병",
    "주식교환",
    "주식이전",
    "영업양도",
    "영업양수",
    "유상증자",          # 자본 변동
    "감자",
    "전환사채",
    "회사채발행",
    "기업지배구조",      # 밸류업 / 거버넌스 자율공시
    "주주가치",          # 주주환원 정책
    "현금배당",          # 배당 결정
    "현물배당",
)


def analyze_significant_filings(filings: list[dict]) -> list[dict]:
    """최근 12M 공시 중 PM 이 *반드시* 검토해야 할 항목 자동 flagging."""
    out = []
    for f in filings:
        nm = (f.get("report_nm") or "").strip()
        # 자사주·5%+·정기보고서·임원변동은 별도 처리 → 제외
        if any(k in nm for k in ("자기주식", "자사주", "임원", "사외이사",
                                  "사업보고서", "분기보고서", "반기보고서",
                                  "감사보고서", "주주명부", "주주총회소집",
                                  "정기주주총회결과", "주식등의대량")):
            continue
        matched = [k for k in SIGNIFICANT_FILING_KEYWORDS if k in nm]
        if matched:
            out.append({
                "rcept_dt": f.get("rcept_dt"),
                "report_nm": nm,
                "rcept_no": f.get("rcept_no"),
                "matched_keywords": matched,
                "pm_review": _pm_review_hint(matched, nm),
            })
    return sorted(out, key=lambda x: x["rcept_dt"], reverse=True)


def _pm_review_hint(keywords: list[str], report_nm: str) -> str:
    """공시 종류별 PM 검토 hint."""
    if "채무보증" in keywords or "타인에대한" in keywords:
        return "⚠️ 자회사/계열사 보증 가능성 — 본문 download 필수, 자본 유출 vs catalyst 판별"
    if "분할" in keywords or "합병" in keywords:
        return "⚠️ 거버넌스 재편 — 분할 비율·신주 배정 확인 필수"
    if "유상증자" in keywords or "감자" in keywords:
        return "⚠️ 자본 구조 변경 — 주주가치 희석 vs 강화 판별"
    if "현금배당" in keywords or "현물배당" in keywords:
        return "✅ 주주환원 — 배당성향 / 자사주 비교 필요"
    if "기업지배구조" in keywords or "주주가치" in keywords:
        return "✅ 밸류업 자율공시 — 시장 약속 vs 실집행 비교"
    if "유형자산처분" in keywords:
        return "✅ 비핵심 자산 매각 — 자본환원 가능성"
    if "타법인주식" in keywords or "출자" in keywords:
        return "⚠️ 자회사 지분 변동 — NAV 영향 확인"
    return f"검토: {', '.join(keywords)}"


# 한국표준산업분류 (KSIC) 2자리 → 산업 컨텍스트 + PM 의미
INDUSTRY_CONTEXT = {
    "10": ("음식료품 제조", "내수 안정, 원재료 환율 영향, 대형사 vs 중소 양극화"),
    "11": ("음료 제조", "내수 안정, 브랜드 가치 큼, 자회사 NAV 큰 경우 多"),
    "13": ("섬유 제조", "사양 산업, M&A · 자산 매각 빈번"),
    "20": ("화학", "사이클 산업, 대형사 종속 多"),
    "21": ("제약·바이오", "R&D 비용 多, 라이센스 가치 분석 필요"),
    "22": ("고무·플라스틱", "OEM 종속 多, 자동차/전자 사이클"),
    "23": ("비금속광물", "건설 사이클 종속, 시멘트 등 대형사 위주"),
    "24": ("1차금속", "사이클 산업, 경기 민감"),
    "25": ("금속 가공", "OEM·B2B 위주, 마진 압박 多"),
    "26": ("전자/IT", "글로벌 경쟁, 기술 변화 빠름"),
    "28": ("전기장비", "OEM 종속, 현대차/LG 그룹사 多"),
    "29": ("일반 기계", "B2B, 사이클 영향"),
    "30": ("자동차/부품", "⚠️ Hyundai/Kia OEM 종속성 강함. 단가 인하(CR) 압박, "
                       "전동화 전환 비용. 일감몰아주기 패턴 일반적"),
    "303": ("자동차 부품", "⚠️ 단일 OEM(주로 현대/기아) 종속 가능성. "
                         "captive customer 우려, 협상력 록인. "
                         "*PM 추론*: 일감 데이터 없어도 매출 50%+ 단일 OEM 가능"),
    "31": ("기타 운송장비", "조선·철도 사이클, 대형사 위주"),
    "33": ("기타 제조", "다양"),
    "35": ("전기/가스", "유틸리티, 규제 산업, PBR 디스카운트 정당"),
    "41": ("건설", "사이클 산업, 자산 多"),
    "46": ("도매", "마진 얇음"),
    "47": ("소매", "내수 안정"),
    "49": ("운송", "사이클, 유가 영향"),
    "52": ("창고·운송보조", "그룹사 captive 多 (예: 한국공항 = 대한항공)"),
    "58": ("출판", "디지털 전환, M&A 多"),
    "62": ("IT 서비스", "B2B 위주, 그룹 captive 多 (예: 삼성SDS)"),
    "63": ("정보 서비스", "구독 모델, 성장"),
    "64": ("금융", "규제 산업"),
    "65": ("보험", "장기 부채 多"),
    "68": ("부동산", "리츠 형태 많음, 장부가 vs 공시지가 중요"),
    "70": ("전문서비스", "다양"),
    "73": ("기타 과학", "다양"),
    "76": ("리스/렌탈", "재무 leverage 큼, 그룹 captive 多 (예: 롯데렌탈)"),
}


def industry_context(induty_code: str | None) -> dict:
    """induty_code → 산업 다이나믹스 + PM 추론 hint."""
    if not induty_code:
        return {"label": "기타", "context": "정보 부족"}
    s = str(induty_code)
    # 정확 매칭 먼저
    if s in INDUSTRY_CONTEXT:
        label, context = INDUSTRY_CONTEXT[s]
        return {"label": label, "context": context, "code": s}
    # 2자리 prefix
    s2 = s[:2]
    if s2 in INDUSTRY_CONTEXT:
        label, context = INDUSTRY_CONTEXT[s2]
        return {"label": label, "context": context, "code": s}
    return {"label": "기타", "context": "산업 매핑 미정의", "code": s}


def compute_pnl_impact(target_pct: float, downside_pct: float,
                       portfolio_size_eok: float = 1000) -> dict:
    """포트폴리오 PnL impact 정량."""
    pos_eok = portfolio_size_eok * target_pct / 100
    pnl_loss_eok = pos_eok * downside_pct / 100
    pnl_impact_pct = pnl_loss_eok / portfolio_size_eok * 100
    return {
        "target_pct": target_pct,
        "downside_pct": downside_pct,
        "position_eok": round(pos_eok, 1),
        "max_loss_eok": round(pnl_loss_eok, 2),
        "portfolio_pnl_impact_pct": round(pnl_impact_pct, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# v5 Phase 1 — 8 helpers (IC-grade 보강)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_profitability_metrics(yf_symbol: str, mcap_won: float,
                                 fy: dict | None = None) -> dict:
    """ROE / ROIC / FCF yield / EV/EBITDA — *행동주의 자본 비효율 leverage 핵심*.

    Cost of equity (KOSPI ~10%) 미달 ROE = 자본환원 demand 정당화.
    """
    out: dict = {}
    try:
        t = yf.Ticker(yf_symbol)
        bs = t.balance_sheet
        cf = t.cashflow
        fin = t.financials

        if bs is not None and not bs.empty:
            col = sorted(bs.columns, reverse=True)[0]

            def get(*keys):
                for k in keys:
                    if k in bs.index:
                        v = bs.loc[k, col]
                        if v is not None and not (isinstance(v, float) and v != v):
                            return float(v)
                return None

            equity = get("Stockholders Equity", "Common Stock Equity")
            total_debt = get("Total Debt") or 0
            cash = get("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments") or 0
            st_inv = get("Other Short Term Investments", "Short Term Investments") or 0

            # NI from fy estimate or yfinance
            ni = (fy or {}).get("net_income_eok")
            ni_won = ni * 1e8 if ni else None
            if not ni_won and fin is not None and not fin.empty:
                ni_col = sorted(fin.columns, reverse=True)[0]
                if "Net Income" in fin.index:
                    v = fin.loc["Net Income", ni_col]
                    ni_won = float(v) if v is not None else None

            oi = (fy or {}).get("operating_income_eok")
            oi_won = oi * 1e8 if oi else None

            # ROE
            if equity and ni_won:
                out["roe_pct"] = round(ni_won / equity * 100, 1)

            # ROIC ≈ NOPAT / (Equity + Debt). NOPAT ≈ OI × (1 - 0.22) (한국 법인세 22%)
            if oi_won and equity is not None:
                ic = equity + (total_debt or 0)
                if ic > 0:
                    nopat = oi_won * 0.78
                    out["roic_pct"] = round(nopat / ic * 100, 1)

            # Net cash position
            net_cash = (cash + st_inv) - (total_debt or 0)
            out["net_cash_eok"] = round(net_cash / 1e8, 0)

            # EV = MC + Debt - Cash
            ev = mcap_won + (total_debt or 0) - cash - st_inv
            out["ev_eok"] = round(ev / 1e8, 0)

            # Equity for ratio
            out["equity_eok"] = round(equity / 1e8, 0) if equity else None
            out["debt_eok"] = round((total_debt or 0) / 1e8, 0)

            # P/B (확인)
            if equity and equity > 0:
                out["pb_check"] = round(mcap_won / equity, 3)

            # EV / EBITDA (EBITDA ≈ OI + D&A. 우리는 OI 만 → 보수적 EV/OI)
            if oi_won and oi_won > 0:
                out["ev_to_oi"] = round(ev / oi_won, 2)

        # FCF yield
        if cf is not None and not cf.empty:
            col = sorted(cf.columns, reverse=True)[0]

            def cfget(*keys):
                for k in keys:
                    if k in cf.index:
                        v = cf.loc[k, col]
                        if v is not None and not (isinstance(v, float) and v != v):
                            return float(v)
                return None

            ocf = cfget("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
            capex = cfget("Capital Expenditure")
            if ocf is not None:
                fcf = ocf + (capex or 0)   # capex is negative
                out["ocf_eok"] = round(ocf / 1e8, 0)
                out["capex_eok"] = round((capex or 0) / 1e8, 0)
                out["fcf_eok"] = round(fcf / 1e8, 0)
                if mcap_won > 0:
                    out["fcf_yield_pct"] = round(fcf / mcap_won * 100, 2)

        # Cost of equity 가정 + 비교
        out["cost_of_equity_assumed_pct"] = 10.0  # KOSPI 평균 가정
        if "roe_pct" in out:
            out["roe_vs_coe_gap_pct"] = round(out["roe_pct"] - 10.0, 1)
            out["roe_below_coe"] = out["roe_pct"] < 10.0
    except Exception:
        pass
    return out


def fetch_peer_comparison(induty_code: str | None, ticker: str,
                          base_date: str | None = None) -> dict:
    """동종 KOSPI peer PBR / PER median + 본 종목 ranking."""
    from activist_scout.domain import industry_peers
    peers = industry_peers(induty_code, exclude_self=ticker, limit=10)
    if not peers:
        return {"peers": [], "label": "산업 매핑 없음"}

    end = base_date or datetime.now().strftime("%Y%m%d")
    rows = []
    try:
        from pykrx import stock as krx
        # 비영업일 처리: 가장 최근 *실 거래일* (PBR > 0 인 데이터) 찾기
        df = None
        base = end
        for d_offset in range(0, 14):
            base = (datetime.strptime(end, "%Y%m%d") - timedelta(days=d_offset)).strftime("%Y%m%d")
            df_kospi = krx.get_market_fundamental(base, market="KOSPI")
            df_kosdaq = krx.get_market_fundamental(base, market="KOSDAQ")
            if df_kospi is not None and not df_kospi.empty and (df_kospi["PBR"] > 0).sum() > 100:
                df = pd.concat([df_kospi, df_kosdaq]) if (df_kosdaq is not None and not df_kosdaq.empty) else df_kospi
                break
        if df is None:
            return {"peers": [], "label": "거래일 데이터 없음"}
        cap_kospi = krx.get_market_cap(base, market="KOSPI")
        cap_kosdaq = krx.get_market_cap(base, market="KOSDAQ")
        cap_df = pd.concat([cap_kospi, cap_kosdaq]) if (cap_kosdaq is not None and not cap_kosdaq.empty) else cap_kospi
        for tk in peers + [ticker]:
            if tk in df.index:
                row = {
                    "ticker": tk,
                    "name": krx.get_market_ticker_name(tk),
                    "PBR": float(df.loc[tk, "PBR"]),
                    "PER": float(df.loc[tk, "PER"]),
                    "시총_억": round(float(cap_df.loc[tk, "시가총액"]) / 1e8, 0)
                                if tk in cap_df.index else None,
                }
                rows.append(row)
    except Exception:
        pass

    # median 계산 (self 제외)
    peer_rows = [r for r in rows if r["ticker"] != ticker and r["PBR"] > 0]
    self_row = next((r for r in rows if r["ticker"] == ticker), None)
    if peer_rows:
        import statistics
        pbr_med = statistics.median(r["PBR"] for r in peer_rows)
        per_med = statistics.median(r["PER"] for r in peer_rows if r["PER"] > 0) if any(r["PER"] > 0 for r in peer_rows) else None
        return {
            "peers": rows,
            "median_pbr": round(pbr_med, 3),
            "median_per": round(per_med, 2) if per_med else None,
            "self_pbr": self_row["PBR"] if self_row else None,
            "self_pbr_vs_median_pct": round((self_row["PBR"] / pbr_med - 1) * 100, 1)
                                      if (self_row and pbr_med > 0) else None,
            "n_peers": len(peer_rows),
        }
    return {"peers": rows, "n_peers": 0}


def fetch_listed_subsidiary_analysis(subsidiaries: list[dict]) -> list[dict]:
    """상장 자회사 직접 분석 — PBR/PER/시총 + 모회사 leverage 의미.

    모회사 보유 22.8% → 22.8% × 자회사 시총 = 모회사 NAV 기여
    그러나 자회사 자체 PBR 이 더 낮으면 *자회사 직접 진입* 이 더 효율적일 수 있음.
    """
    out = []
    try:
        from activist_scout.utils import load_corp_map
        from pykrx import stock as krx
        cm = load_corp_map()
        # corp_name → stock_code 인덱스 만들기
        name_to_stock = {}
        for sc, info in cm.items():
            nm = (info.get("corp_name", "") or "").replace("(주)", "").replace("주식회사", "").replace(" ", "").strip()
            if nm:
                name_to_stock[nm] = sc

        end = datetime.now().strftime("%Y%m%d")
        df = None
        for d_offset in range(0, 14):
            base = (datetime.strptime(end, "%Y%m%d") - timedelta(days=d_offset)).strftime("%Y%m%d")
            df_kospi = krx.get_market_fundamental(base, market="KOSPI")
            df_kosdaq = krx.get_market_fundamental(base, market="KOSDAQ")
            if df_kospi is not None and not df_kospi.empty and (df_kospi["PBR"] > 0).sum() > 100:
                df = pd.concat([df_kospi, df_kosdaq]) if (df_kosdaq is not None and not df_kosdaq.empty) else df_kospi
                break
        if df is None:
            return []
        cap_kospi = krx.get_market_cap(base, market="KOSPI")
        cap_kosdaq = krx.get_market_cap(base, market="KOSDAQ")
        cap_df = pd.concat([cap_kospi, cap_kosdaq]) if (cap_kosdaq is not None and not cap_kosdaq.empty) else cap_kospi

        for s in subsidiaries:
            nm_raw = s.get("name") or ""
            nm = nm_raw.replace("(주)", "").replace("주식회사", "").replace("㈜", "").replace(" ", "").strip()
            if not nm or nm == "합계":
                continue
            sub_stock = name_to_stock.get(nm)
            if not sub_stock or sub_stock not in df.index:
                continue
            sub_pbr = float(df.loc[sub_stock, "PBR"])
            sub_per = float(df.loc[sub_stock, "PER"])
            sub_mcap = float(cap_df.loc[sub_stock, "시가총액"]) / 1e8 if sub_stock in cap_df.index else 0
            stake = (s.get("stake_pct") or 0) / 100
            out.append({
                "name": nm_raw,
                "stock_code": sub_stock,
                "stake_pct": s.get("stake_pct"),
                "subsidiary_PBR": round(sub_pbr, 3),
                "subsidiary_PER": round(sub_per, 2),
                "subsidiary_mcap_eok": round(sub_mcap, 0),
                "parent_share_eok": round(sub_mcap * stake, 0),
            })
    except Exception:
        pass
    return out


def fetch_foreign_ownership_trend(ticker: str, days: int = 90) -> dict:
    """외국인 매수/매도 추세 — 외국계 fund 진입의 "약한 시그널"."""
    try:
        from pykrx import stock as krx
        end = datetime.now().strftime("%Y%m%d")
        bgn = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        df = krx.get_market_trading_value_by_date(bgn, end, ticker)
        if df is None or df.empty or "외국인합계" not in df.columns:
            return {}
        last90 = df.tail(60)
        net_value = float(last90["외국인합계"].sum())
        days_buy = int((last90["외국인합계"] > 0).sum())
        days_total = len(last90)
        # 한도 소진율 (가능 시)
        try:
            er = krx.get_exhaustion_rates_of_foreign_investment(end, ticker)
            er_pct = float(er.iloc[0]["지분율"]) if er is not None and not er.empty else None
        except Exception:
            er_pct = None
        return {
            "foreign_net_value_eok_60d": round(net_value / 1e8, 1),
            "buy_days_60d": days_buy,
            "total_days_60d": days_total,
            "ownership_pct": er_pct,
        }
    except Exception:
        return {}


def fetch_executive_compensation(corp_code: str, year: int,
                                  oi_eok: float | None = None,
                                  mcap_eok: float | None = None) -> dict:
    """임원 보수 — 회사 성과 대비 alignment 분석.

    행동주의의 흔한 공격 포인트: 보수/영업이익 비율 비정상.
    """
    try:
        j = dart_get(
            "hmvAuditAllSttus.json",
            {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
        )
        if not j or j.get("status") != "000":
            return {}
        rows = j.get("list", [])
        # 이사·감사 전체 보수 총액 (단위: 천원)
        total_won = 0
        n_persons = 0
        for it in rows:
            try:
                amount = float((it.get("nmpr") or "0").replace(",", "")) * 1000   # 천원→원
                count = int((it.get("mendng_totamt") or "1").replace(",", "")) if it.get("mendng_totamt") else int((it.get("hmpw") or "1") or "1")
                total_won += amount
                if count:
                    n_persons += count
            except Exception:
                pass
        if total_won == 0:
            return {}
        out = {
            "total_compensation_eok": round(total_won / 1e8, 1),
            "n_persons": n_persons,
        }
        if oi_eok and oi_eok > 0:
            out["comp_to_oi_pct"] = round((total_won / 1e8) / oi_eok * 100, 1)
        if mcap_eok and mcap_eok > 0:
            out["comp_to_mcap_bps"] = round((total_won / 1e8) / mcap_eok * 10000, 1)
        return out
    except Exception:
        return {}


def analyze_treasury_history(treasury_disclosures: list[dict]) -> dict:
    """자사주 공시 5년 timeline + direction별 카운트.

    *patterns*:
    - 매입(취득) > 처분 + 소각 → 우호적
    - 처분/신탁해지 > 매입 → 비우호적
    """
    if not treasury_disclosures:
        return {"timeline": [], "summary": "5Y 자사주 공시 없음"}
    by_year: dict[str, dict] = {}
    timeline = []
    for t in sorted(treasury_disclosures, key=lambda x: x.get("rcept_dt", "")):
        d = t.get("rcept_dt", "")[:4] if len(t.get("rcept_dt", "")) >= 4 else "?"
        direction = t.get("direction", "other")
        by_year.setdefault(d, {}).setdefault(direction, 0)
        by_year[d][direction] += 1
        timeline.append({"year": d, "direction": direction, "report_nm": t.get("report_nm")})
    return {"timeline": timeline[-10:], "by_year": by_year}


def fetch_5y_price_history(ticker: str) -> dict:
    """5년 주가 추이 + 거래정지 detect + 현재가의 5년 high/low 위치."""
    try:
        from pykrx import stock as krx
        end = datetime.now().strftime("%Y%m%d")
        bgn = (datetime.now() - timedelta(days=5 * 365)).strftime("%Y%m%d")
        df = krx.get_market_ohlcv(bgn, end, ticker)
        if df is None or df.empty:
            return {}
        # 거래정지 detect: 거래량 0 인 연속 일수
        zero_volume_days = int((df["거래량"] == 0).sum())
        df_traded = df[df["거래량"] > 0]
        if df_traded.empty:
            return {"zero_volume_days_5y": zero_volume_days}
        high = float(df_traded["종가"].max())
        low = float(df_traded["종가"].min())
        current = float(df_traded["종가"].iloc[-1])
        # 거래재개 시점 추정 (가장 긴 연속 0거래 후 첫 거래일)
        return {
            "5y_high": int(high),
            "5y_low": int(low),
            "current": int(current),
            "current_vs_high_pct": round((current / high - 1) * 100, 1),
            "current_vs_low_pct": round((current / low - 1) * 100, 1),
            "zero_volume_days_5y": zero_volume_days,
            "traded_days": len(df_traded),
            "has_halt_history": zero_volume_days > 30,
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 2) 회계연도 인지 catalyst calendar
# ─────────────────────────────────────────────────────────────────────────────

def compute_catalyst_calendar(acc_mt: str | None) -> dict:
    """회계연도 (acc_mt) 에 따라 다음 정기주총·주주제안 마감 자동 산출.

    한국 상장사 기준: 결산월 + 3개월 = 정기주총. 주주제안 마감은 주총 6주 전.

    예: acc_mt="06" → 6월 결산 → 9월 주총 → 8월 중순 마감
    """
    today = datetime.now()
    try:
        mt = int(acc_mt or "12")
    except Exception:
        mt = 12
    agm_month = ((mt + 3 - 1) % 12) + 1     # 결산 + 3 개월

    year = today.year
    agm = datetime(year, agm_month, 28)
    if agm < today:
        agm = datetime(year + 1, agm_month, 28)
    proposal_deadline = agm - timedelta(weeks=6)

    return {
        "acc_mt": mt,
        "agm_month": agm_month,
        "next_agm": agm.strftime("%Y-%m-%d"),
        "proposal_deadline": proposal_deadline.strftime("%Y-%m-%d"),
        "days_to_agm": (agm - today).days,
        "days_to_deadline": (proposal_deadline - today).days,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3) Bundle 빌드
# ─────────────────────────────────────────────────────────────────────────────

def build_bundle(ticker: str, year: int, extra_context: str | None = None) -> dict:
    enr, sc = load_existing(ticker)
    co = enr.get("company") or {}
    corp_code = enr.get("corp_code")
    yf_symbol = f"{ticker}.KS"

    print(f"[{ticker}] 데이터 수집 중...")
    shareholders = fetch_shareholder_detail(corp_code, year)
    recent_filings = fetch_recent_disclosures(corp_code, days=365)
    treasury = fetch_treasury_holdings(corp_code, year)
    calendar = compute_catalyst_calendar(co.get("acc_mt"))
    qfin = fetch_quarterly_financials(yf_symbol)
    bsheet = fetch_balance_sheet(yf_symbol)
    holdings_raw = enr.get("major_holdings_5pct") or []
    holdings_trend = analyze_5pct_trend(holdings_raw)
    subs = enr.get("subsidiaries") or []
    implied_nav = compute_implied_nav(subs)

    # 시장임팩트 (liquidity.csv 기반)
    import pandas as pd
    from activist_scout.config import LIQUIDITY_CSV
    liq = {}
    try:
        liq_df = pd.read_csv(LIQUIDITY_CSV, dtype={"ticker": str})
        row = liq_df[liq_df["ticker"] == ticker]
        if not row.empty:
            liq = row.iloc[0].to_dict()
    except Exception:
        pass
    mcap_eok = (enr.get("fundamentals") or {}).get("시가총액(억)")
    market_impact_15 = compute_market_impact(liq.get("ADV_20D(억)"), mcap_eok, target_pct=1.5)
    market_impact_05 = compute_market_impact(liq.get("ADV_20D(억)"), mcap_eok, target_pct=0.5)

    # v4 추가: 큰 공시 자동 flagging + 산업 컨텍스트 + PnL impact
    significant = analyze_significant_filings(recent_filings)
    industry = industry_context(co.get("induty_code"))
    pnl_15 = compute_pnl_impact(1.5, 25.0)
    pnl_05 = compute_pnl_impact(0.5, 25.0)

    # v5 Phase 1: IC-grade 8 helpers
    print(f"  · v5 helpers — profitability / peers / 자회사 / 외국인 / 보수 / 자사주history / 5y price")
    mcap_won = (mcap_eok or 0) * 1e8
    profitability = fetch_profitability_metrics(yf_symbol, mcap_won, qfin.get("fy_estimate"))
    peer_comp = fetch_peer_comparison(co.get("induty_code"), ticker)
    listed_subs = fetch_listed_subsidiary_analysis(subs)
    foreign_trend = fetch_foreign_ownership_trend(ticker)
    exec_comp = fetch_executive_compensation(corp_code, year,
        oi_eok=qfin.get("fy_estimate", {}).get("operating_income_eok"),
        mcap_eok=mcap_eok)
    treasury_hist = analyze_treasury_history(enr.get("treasury_disclosures") or [])
    price_5y = fetch_5y_price_history(ticker)
    print(f"  · ROE: {profitability.get('roe_pct','?')}%, FCF yield: {profitability.get('fcf_yield_pct','?')}%, Cost of Equity gap: {profitability.get('roe_vs_coe_gap_pct','?')}%")
    print(f"  · Peers: {peer_comp.get('n_peers',0)}명, 동종 median PBR {peer_comp.get('median_pbr','?')} vs 본 종목 {peer_comp.get('self_pbr','?')}")
    print(f"  · 상장 자회사: {len(listed_subs)}개, 외국인 60D 순매수 {foreign_trend.get('foreign_net_value_eok_60d','?')}억")
    print(f"  · 5Y 주가: 현재가 vs 5Y high {price_5y.get('current_vs_high_pct','?')}%, 거래정지일 {price_5y.get('zero_volume_days_5y','?')}일")

    print(f"  · 최대주주 명단: {len(shareholders)}명")
    print(f"  · 최근 12M 공시: {len(recent_filings)}건")
    print(f"  · 분기 financials: {len(qfin.get('quarters',[]))}개 분기")
    print(f"  · 5%+ 보고자 시계열: {len(holdings_trend.get('per_filer',[]))}명, 외부 5%+ {holdings_trend['summary']['external_5pct_count']}건")
    print(f"  · 자회사 implied NAV: 장부가 {implied_nav['book_total_eok']}억 → 자산기반 {implied_nav['asset_implied_total_eok']}억 ({implied_nav.get('uplift_x','?')}배)")
    print(f"  · 산업: {industry.get('label')} ({industry.get('code')})")
    print(f"  · 큰 공시 (12M): {len(significant)}건 자동 flagging")
    print(f"  · 회계연도: {calendar['acc_mt']}월 결산 → 다음 주총 {calendar['next_agm']} (D-{calendar['days_to_agm']})")

    return {
        "ticker": ticker,
        "name": enr["name"],
        "corp_code": corp_code,
        "company": co,
        "fundamentals": enr.get("fundamentals") or {},
        "score": {
            "tier": sc["tier"],
            "target_score": sc["target_score"],
            "accum_score": sc["accum_score"],
            "legal_score": sc["legal_score"],
            "target_hits": sc.get("target_hits", []),
            "accum_hits": sc.get("accum_hits", []),
            "legal_hits": sc.get("legal_hits", []),
            "avoid_reason": sc.get("avoid_reason"),
            "sanity_flags": sc.get("sanity_flags", []),
        },
        "flow": enr.get("flow") or {},
        "summary": enr.get("summary") or {},
        "subsidiaries": subs,
        "exec_tenure": enr.get("exec_tenure") or [],
        "treasury_disclosures": enr.get("treasury_disclosures") or [],
        "governance_disclosures": enr.get("governance_disclosures") or [],
        "major_holdings_5pct": holdings_raw,
        "related_party_analysis": enr.get("related_party_analysis") or {},
        "nav": sc.get("nav"),
        "post_filing": sc.get("post_filing"),
        "shareholders_detail": shareholders,
        "recent_filings_12m": recent_filings,
        "treasury_holdings": treasury,
        "catalyst_calendar": calendar,
        # v2 추가:
        "quarterly_financials": qfin,           # 분기 매출/OI/NI + FY 합계
        "balance_sheet": bsheet,                 # 정확 부채/자본/자산
        "holdings_trend": holdings_trend,        # 5%+ 시계열 + 외부 매도/매수
        "implied_nav": implied_nav,              # 자회사 자산 × 지분 implied
        "liquidity": liq,                        # ADV + capacity
        "market_impact": {
            "target_1_5pct": market_impact_15,
            "target_0_5pct": market_impact_05,
        },
        "significant_filings_12m": significant,  # v4
        "industry": industry,                     # v4
        "pnl_impact": {                           # v4
            "target_1_5pct": pnl_15,
            "target_0_5pct": pnl_05,
        },
        # v5 Phase 1 — IC-grade 8 helpers:
        "profitability": profitability,           # ROE/ROIC/FCF yield/EV/EBITDA
        "peer_comparison": peer_comp,             # 동종 PBR/PER median + ranking
        "listed_subsidiaries": listed_subs,       # 상장 자회사 직접 분석
        "foreign_ownership_trend": foreign_trend, # 외국인 60D 추세
        "executive_compensation": exec_comp,      # 임원 보수 alignment
        "treasury_history": treasury_hist,        # 자사주 5년 timeline
        "price_history_5y": price_5y,             # 5년 주가 + 거래정지 detect
        "extra_context": extra_context,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4) LLM 호출 — OpenAI structured output
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 한국 행동주의 헤지펀드의 시니어 PM 입니다 (15년 경력, 한국 활동주의 캠페인 다수 참여).
**최고 수준의 PM-grade deep dive markdown 보고서**를 작성합니다. 정보 보고서가 아닌, *PM이 동료 PM 에게 reporting* 하는 의사결정 문서입니다.

# CORE PM 룰 (8개) — *모두 준수*

## R1. SYNTHESIS — 첫 줄 thesis 강제
보고서 §0 자리에 **한 줄 thesis** 형식 강제:
> "**Technically [형용사] but [한계] → unique trigger path is [Z]**"

예: "**Technically 한국 행동주의 textbook setup but 가족·재단·holdco 일치 컨트롤로 leverage 약화 → unique trigger path is 외국계 펀드 진입 + 신상법 주주대표소송**"

이 한 줄이 PM 의사결정의 *core synthesis*. 정보 나열 ❌, 단호한 판단 ⭕.

## R2. INDUSTRY CONTEXT — *bundle.industry* 활용
모든 펀더멘털 / 일감몰아주기 / 거버넌스 분석에 산업 다이나믹스 통합.
- `industry.label` 과 `industry.context` 인용
- 자동차부품 → "OEM 종속, 단가 인하 압박" 자동 framing
- 식음료 → "내수 안정, 자회사 NAV 큰 경우 多" 등

## R3. CAPTIVE 추론 — 데이터 없을 때 PM 추론
일감몰아주기 데이터가 없거나 confidence low 라도 *추론* 필수:
- 산업이 자동차부품 + 자회사 다수 → "OEM 단일고객 가능성, 일감 데이터 없어도 매출 50%+ 단일 OEM 추정"
- 그룹사 captive 의심 → "captive customer 우려, 행동주의 진입 시 무력화 위험"

## R4. PM TONE — 단호함 강제
- ❌ 금지: "이유 1, 2, 3, 4..." / "WATCH 유지가 적절합니다" / "추가 확인이 필요합니다"
- ⭕ 강제: "X 이므로 Y" / "5분 검토로는 매수 결정 X, 1주 deep dive 후 진입 결정"
- 결론 톤: *contingent reasoning* (if-then 매트릭스), *명확한 trigger path*

## R5. DRAMA / EMPHASIS
핵심 implied NAV 같은 수치는 **극적 대조** 형식 강제:
- ❌ "implied 1,054억"
- ⭕ "**자회사 자산 4,625억 × 22.8% = 1,054억 — 장부가 233억의 *5배***"

implied NAV 합계도: "*장부가 851억 vs 자산기반 3,770억 — uplift 4.43배*. **즉 시장은 자회사를 거의 무료 취급 중**"

## R6. SIGNIFICANT FILING 분석 강제 — *bundle.significant_filings_12m*
최근 12M 자동 flagging 된 큰 공시 *모두* 별도 sub-section 으로 specific 해석:
- "2026-02-23 타인에 대한 채무보증 결정 → **자회사 보증 가능성. 비핵심 자회사 출자라면 자본환원 demand 의 직접 trigger**"
- 본문 download 필요한 항목은 ⚠️ 표시 + Day 1 작업으로 매핑
- 단순 list X, *재무/거버넌스 implication* 명시

## R7. RISK BUDGET — *bundle.pnl_impact* 활용
사이즈 권고에 portfolio PnL impact 정량 강제:
- "1.5% × 다운사이드 25% = portfolio PnL impact -0.4%"
- "0.5% 진입 시 risk budget -0.13% 만 소비"
- portfolio 1,000억 가정 시 절대값도 명시

## R8. WORKFLOW CADENCE — 의사결정 cadence 명시
- "5분 검토" → 시스템 결과 + 본 보고서 read
- "1시간 보강" → significant filings 본문 download + 사외이사 임기 cross-check
- "1주 deep dive" → DART §V·§VIII 직접 + 매니지먼트 회의 시도
- "1개월" → IC 메모 + 진입/패스 결정

각 PM Task 를 cadence 별로 매핑.

---

# 데이터 해석 원칙 (CRITICAL — 절대 준수)

## A. 매출 trend
`fundamentals.매출_4Y(억원)` 와 `quarterly_financials.fy_estimate.revenue_eok` 둘 다 비교.
**금지**: 4Y 만 보고 단정. 분기 cross-check 필수.

## B. 부채
`balance_sheet.total_debt_eok` 사용. NaN/0 이면 "DART §V 직접 확인 필요" 명시.

## C. 자회사 NAV (R5 와 결합)
`nav.total_nav_eok` (장부가) + `implied_nav.asset_implied_total_eok` (자산기반) 둘 다 명시.
*반드시* uplift 배수 + drama framing.

## D. 외부 5%+
`holdings_trend.external_5pct` 매수/매도 방향성 분석.

## E. extra_context
사용자 제공 historical context 가 있으면 §6 거버넌스에 통합.

## F. INDUSTRY (R2)
`bundle.industry.label` + `context` 모든 분석에 통합.

## G. SIGNIFICANT FILINGS (R6)
`bundle.significant_filings_12m` *모두* 분석.

---

# 12+1 섹션 작성 가이드

# 데이터 해석 원칙 (CRITICAL — 절대 준수)

## A. 매출 trend 해석 (가장 중요)
`fundamentals.매출_4Y(억원)` 은 *연결 누계 4년* 데이터로 형식이 회사마다 다를 수 있음.
*반드시* `quarterly_financials.fy_estimate.revenue_eok` (최근 4 분기 합산) 와 비교 후 해석.
`quarterly_financials.fy_estimate.qoq_revenue` 의 분기별 추세로 판단.
**금지**: 4Y 데이터만 보고 "매출 감소/증가 추세" 단정 — 분기 데이터로 cross-check 필수.

## B. 부채 데이터
`balance_sheet.total_debt_eok` 사용. NaN 또는 0 이면 *명시적으로 "DART 사업보고서 §V 직접 확인 필요"* 라고 표기.

## C. 자회사 NAV 해석 (필수)
`nav.total_nav_eok` (장부가 기반) 와 `implied_nav.asset_implied_total_eok` (자산 × 지분 기반)
*둘 다 명시*. PM 해석: 비상장 자회사는 장부가가 *원시 취득가* 라 **5~10배 underestimate** 가능.
*반드시*: "장부가 기준 NAV X억 vs 자산기반 implied Y억 (uplift Z배)" 형식으로 표기.

## D. 외부 5%+ 추세
`holdings_trend.external_5pct` 의 매수/매도 방향성 *반드시* 분석.
"외부 5%+ N건 중 M건 매도" → 매집 capacity 의미 명시.

## E. 매집 시장임팩트 (정량)
`market_impact.target_1_5pct` 사용. 목표 % × 시총 = target_eok / 일평균 매수 = 매집 일수.
"1.5% 진입 시 X일 매집 (ADV의 5% 기준)" 형식 강제.

## F. extra_context 활용
사용자 제공 *historical context* (배임 이력, 외부 사건 등) 가 있으면 §6 거버넌스 섹션에 통합.

---

# 13-section 작성 가이드 (§0 ~ §12)

## §0. PM Thesis (R1 강제, 1줄 — 보고서 head)
**format**: `> **Technically [형용사] but [한계] → unique trigger path is [Z]**`
이 한 줄이 보고서 전체 결론.

## §1. 회사 개요 (메타 표 + 1단락)
표: 회사명/대표/본사/설립/업종/회계연도/시총/현재가
1단락 (R2 강제): `bundle.industry.label` + `context` 인용. 산업 다이나믹스 1줄 명시.
예: "induty=303 (자동차 부품) — *⚠️ 단일 OEM (현대/기아) 종속 가능성, captive customer 우려*"

## §2. 펀더멘털 스냅샷
표 컬럼: 항목 / 값 / 해석
- PBR / PER / 시총 / 최대주주 지분율 / 잉여자본비율 / 순현금
- **매출 (FY 추정 4Q 합)**: quarterly_financials.fy_estimate.revenue_eok
- **OPM (FY 추정)**: quarterly_financials.fy_estimate.opm_pct
- **순이익 (FY 추정)**: net_income_eok
- 마지막 행: "순이익 / 영업이익 비율" → 자회사 지분법 평가이익 비중 추정

## §2.5. **수익성 분석 (R9 NEW v5)** — `bundle.profitability`
*행동주의의 자본 비효율 leverage 핵심* — 이 섹션 *반드시* 별도 작성:
- ROE / ROIC 인용 + Cost of Equity 10% 비교
- "**ROE X% < CoE 10% → Y% 갭. 자본 비효율 명백, 행동주의 자본환원 demand 정당화**"
- FCF yield 인용 + "FCF yield X% (시총 대비) → 배당여력 정량"
- EV/OI (또는 EV/EBITDA), net cash position 명시
- Cost of Equity gap이 음수면 *명시적으로* "행동주의 핵심 leverage" 강조

## §3. Sum-of-parts NAV (R5 drama 강제)
- 자회사 표: 이름 / 구분(상장/비상장) / 지분 / 장부가 / 자산 / 순이익 / **자산기반 implied**
- *각 자회사 implied 마다* "장부가 X억의 N배" 형식 강제
- NAV 합계 + drama 강제: "**장부가 851억 vs 자산기반 3,770억 — uplift 4.43배. 시장은 자회사를 거의 무료 취급 중**"

## §3.5. **상장 자회사 직접 분석 (R10 NEW v5)** — `bundle.listed_subsidiaries`
상장 자회사가 있는 경우 *반드시* sub-section 생성:
- 자회사 PBR / PER / 시총 인용
- 모회사 보유 share value (자회사 시총 × 지분)
- *PM 판단*: "자회사 PBR < 모회사 PBR이면 *자회사 직접 진입이 더 효율적*일 수 있음" 명시
- 활동주의 캠페인의 *cross-investment leverage* 가능성 분석

## §3.6. **Peer comparison (R11 NEW v5)** — `bundle.peer_comparison`
동종 KOSPI peer 비교 *반드시*:
- 표: 종목 / PBR / PER / 시총
- median PBR + 본 종목 PBR vs median 차이 % (drama 형식)
- *PM 결론*: "동종 median 대비 N% 디스카운트 → 단순 sector beta 인지 idiosyncratic 인지 판별"
- nav.discount_pct (장부가 기준) + implied uplift 비교 → "보수적 NAV X억, 자산기반 Y억"
- nav.trust 명시 (HIGH/MEDIUM/LOW)

## §4. 잉여자본 (정량 박스)
```
현금                  X억
단기투자              Y억
─────
순유동성             X+Y억
총부채               (balance_sheet.total_debt_eok 또는 "DART 검증 필요")
─────
순현금               (계산값)
시총                  M억
잉여자본비율 (NC/MC) Z배
```
balance_sheet 부채 데이터 unavailable 시 "yfinance 부재 → DART 사업보고서 §V 직접 확인 필요" 명시.

## §5. 최대주주 구조
shareholders_detail 표 + 가족경영/재단/holdco 패턴 분석.
**+ 외부 5%+ 보유자 (holdings_trend.external_5pct) 별도 표시**:
- "이름, 지분, 시계열 변동 (매수 +0.5% / 매도 -1.5% 등), 매수/매도 방향"
- 매도 추세는 "매집 capacity 잔여" 의미 명시

## §5.5. **외국인 지분 추세 (R12 NEW v5)** — `bundle.foreign_ownership_trend`
외국인 60D 매수/매도 + 보유율 명시:
- "60일 외국인 순매수 X억 (매수일 N/M)"
- 외국인 한도 소진율 (있으면) 인용
- *PM 판단*: 매수 추세면 "외국계 fund 진입 약한 시그널 — 추가 monitor", 매도면 "패스"

## §6. 거버넌스 record
- governance_disclosures (incident 5Y) 명시
- exec_tenure 사외이사 임기 만료 임박 명시
- *extra_context 가 있으면 통합* (예: 4,200억 배임 + 거래정지 3년 등)
- post-amendment 상법 leverage 평가

## §6.5. **임원 보수 alignment (R13 NEW v5)** — `bundle.executive_compensation`
보수 / 영업이익 비율 + 보수 / 시총 bps:
- "이사·감사 N명 총 보수 X억 — 영업이익 대비 Y%, 시총 대비 Z bps"
- 비교: 통상 5%~10% (영업이익 대비) → 그 이상이면 *비합리* 시그널
- *PM 판단*: 보수 비합리 → 행동주의 캠페인의 *명시적 안건* (보수 정책 변경 demand)

## §6.6. **자사주 매입/처분 5Y History (R14 NEW v5)** — `bundle.treasury_history`
연도별 direction count timeline:
- 표: 연도 / burn / buy_done / dispose / trust_cancel
- *PM 해석*: 매입 > 처분이면 "자사주 정책 trend 우호적", 반대면 "비우호적"
- 단순 *최근 1건* 이 아니라 *5년 trend* 로 진정성 평가

## §7. Catalyst Timing
- catalyst_calendar 정확 인용 (회계연도 + 다음 주총 + 주주제안 마감 + D-Day)
- 6월 결산 vs 12월 결산 차이 명시
- 사외이사 임기 + 주총 동시 만료 patterns 강조

## §8. 행동주의 진입 신호 (4 sub-section)
- 5%+ filing 신규 (recent_activist_filings_12M)
- 기관 매집 (flow.기관순매수_90D, 매수일/총, vs VWAP)
- secrecy (accum_hits 의 stake_secrecy 룰 트리거 여부)
- 종합: "stake building 단계 / catalyst 임박 / etc."

## §8.5. 큰 공시 자동 분석 (R6 강제) — *bundle.significant_filings_12m*
이 섹션은 *반드시* 별도. 자동 flagging 된 모든 공시 sub-section 단위로 specific 해석:
```
### 2026-02-23 타인에 대한 채무보증 결정
**PM 해석**: 자회사 보증 가능성. 비핵심 자회사 출자라면 → *자본환원 demand 의 직접 trigger*
**Day 1 작업**: DART 본문 download → 보증 대상·금액·만기 확인
```
모든 flagging 공시에 *재무/거버넌스 implication* 명시. 단순 list ❌.
없으면 "**12M 큰 공시 없음** — *catalyst 부재* 신호" 1줄.

## §9. 일감몰아주기 (정확값 또는 추가 검증, R3 captive 추론 결합)
- related_party_analysis.ratio_pct + confidence 인용
- ratio < 5% 또는 confidence low → "데이터 부족, 사업보고서 §VIII 직접 검증 필수" + 검증 항목 list
- **R3 강제**: 데이터 없을 때 산업/자회사 패턴으로 *추론* 필수.
  예: "industry=자동차부품 + 자회사 5개 모두 '세원' prefix → *추정 매출 50%+ 단일 OEM 가능성. captive customer 우려*"

## §9.5. **5Y 주가 history + 거래정지 회복도 (R15 NEW v5)** — `bundle.price_history_5y`
- 5Y high / low / 현재가 위치 ("현재가는 5Y high 대비 N%")
- 거래정지 detect (zero_volume_days_5y) — 30일 이상이면 "거래정지 이력"
- *PM 판단*: 거래재개 후 회복도 = 시장의 *재신뢰* 정도 측정. 미회복 = 시장이 여전히 회의적.

## §10. 진입 권고 — **점수 매트릭스 형식 강제**
다음 형식 그대로 사용:

| 차원 | 시그널 | 점수 |
|---|---|---|
| 잉여자본 | (현금/시총 비율) | +30 |
| Catalyst 임박 | (사외이사 임기 D-XX) | +20 |
| 매집 capacity | (외부 5%+ 매도) | +15 |
| (...) | | |
| **누적 점수** | | **N** |
| **위험 (negative)** | | |
| 거버넌스 사고 | (3건 5Y) | -30 |
| (...) | | |
| **순 점수** | | **N** |

그리고 **net 점수 →** CONSIDER/WATCH/AVOID 매핑:
- 70+ → CONSIDER (1.5%~2% 진입)
- 40~70 → WATCH (조건부)
- <40 → 보류
- 음수 → AVOID

## §10.5. **Catalyst Probability × Impact Matrix (R16 NEW v5)** — *반드시* 표 형식
다음 형식 강제 (자체 추정값. 데이터 없으므로 *PM judgment*):

| Catalyst | 12M 발현 P | 발현 시 영향 (%) | EV (P × Impact) |
|---|---|---|---|
| 사외이사 추천 통과 | 25% | +15% | +3.75% |
| 자사주 매입/소각 결의 | 15% | +10% | +1.5% |
| 외국계 펀드 5%+ 진입 | 10% | +25% | +2.5% |
| 자회사 매각/IPO | 5% | +30% | +1.5% |
| **upside EV 합계** | | | **+9.25%** |
| **거버넌스 사고 재발 (downside)** | 5% | -25% | -1.25% |
| **순 EV** | | | **+8.0%** |

*PM 판단*:
- 순 EV > 15% → 적극 사이즈 (1~2%)
- 5~15% → 보수 진입 (0.5~1%)
- < 5% → 패스

본 종목 데이터·산업·거버넌스에 *fit* 하게 확률 추정. catalyst 항목은 보고서 종목별로 cusumize.

## §11. 운영 plan (specific schedule)
**1.5% 진입 시 + 0.5% 보수 진입 시 *둘 다* 시장임팩트 정량 표 형태로 비교**:

| 사이즈 | 절대값 | 일평균 매수(ADV 5%) | 매집 일수 | 매집 주수 |
|---|---|---|---|---|
| 1.5% | (target_1_5pct.target_eok) 억 | (daily_buy_5pct_adv_eok) 억/일 | (estimated_days_to_fill) 일 | (weeks_to_fill) 주 |
| 0.5% | (target_0_5pct.target_eok) 억 | (daily_buy_5pct_adv_eok) 억/일 | (target_0_5pct.estimated_days_to_fill) 일 | (target_0_5pct.weeks_to_fill) 주 |

매집 일수 ≥ 100일 인 경우 **"유동성 제약. 0.5% 또는 5% 신탁/장외 블록 거래 검토"** 명시.

```
매집 phase (D-X ~ D-Y):
  - 1.5% target: 일평균 매수 N억 → K주 매집
  - 평균매수가 목표: A~B 원 (현재가 ±5%)

캠페인 phase (D-Y ~ D-Z):
  - 외부 5%+ 매도자 접촉
  - 신임 사외이사 후보 추천 준비
  - 자사주 매입/소각 demand letter

Exit conditions:
  - 정기주총 표결 통과 → +X% upside, 1년 hold
  - 부결 → 즉시 exit (-X~Y% loss)
  - 신규 횡령/감사 사고 → 즉시 청산
```

market_impact.target_1_5pct + target_0_5pct 데이터 *둘 다 사용*.

## §11.5. 자본 기반 NAV 확장 (optional, 데이터 가능 시)
balance_sheet.stockholders_equity_eok 와 자회사 자본 비교:
- 본사 자기자본 vs 시총 = (book_value 디스카운트)
- 자회사 implied 자본기반 NAV (자산 - 자회사 부채 추정 [Total Liab × stake]) 구체 분석
- "장부가 / 자산기반 / 자본기반" 3단 비교 NAV.

데이터 부족 시: "자회사 부채 정보 미제공 → 자산기반 implied 가 upper bound, 자본기반은 50~70% 수준 추정"

## §12. 종합 결론 (R4 PM tone + R7 PnL impact + R8 workflow)
- §0 thesis 재인용 + 결론 액션 (CONSIDER/WATCH/AVOID)
- **R7 강제** — 사이즈 + portfolio PnL impact:
  ```
  사이즈 1.5% (절대값 23.0억) × 다운사이드 25% = portfolio PnL impact -0.4%
  사이즈 0.5% (절대값 7.7억) × 다운사이드 25% = portfolio PnL impact -0.13%
  Risk budget: 0.5% 안 (-0.13%) → 0.5% 진입 권고
  ```
  `bundle.pnl_impact.target_1_5pct` + `target_0_5pct` 활용.

- **R8 강제** — Workflow cadence 매핑:
  ```
  ⏱ 5분 (지금): 본 deep dive read → 진입 결정 X, 1주 deep dive 필요 인지
  ⏱ 1시간 (오늘): significant filings 본문 download (R6 항목 매핑)
                + 사외이사 임기 cross-check (DART 임원공시)
  ⏱ 1주 (이번주): DART §V 부채 + §VIII 일감 직접 + 매니지먼트 회의 시도
                + 외국계 fund 한국 입국 monitor
  ⏱ 1개월 (이번달): IC 메모 + 진입/패스 결정
  ```

- *Unique trigger path* 명시 (R1 thesis 와 일관)

## §13. ⚠️ 자동화 외 PM 영역 (사람 검증 필수)
시스템이 자동으로 할 수 *없는* 항목 — 보고서 끝에 *반드시* 명시:

```
- 외국계 펀드 한국 입국 신호 (Bloomberg / 헤드헌터 / 미디어)
- 외부 5%+ 보유자 신원 조회 (LinkedIn / 공시 cross-ref)
- significant_filings 의 본문 직접 download (LLM 은 제목만 봄)
- 매니지먼트 인터뷰 / 산업 가십 / 경쟁사 동향
- IC 동료 PM·법률 자문 검토
- 분기 사업보고서 §V·§VIII 직접 검증 (LLM 추출은 보조)
```

---

# 출력 톤
- *PM이 동료 PM 에게 reporting* 하는 톤. 정중하지만 단호함.
- 데이터 인용 시 정확한 숫자 (반올림 X) + 출처 필드명 괄호로 명시
- "검증 필요" 항목은 빨간 색 (⚠️) + Day 1~7 작업에 매핑

위 12개 섹션 모두 작성. 각 섹션 *숫자 풍부*. 생략 금지."""


def call_openai_deep_dive(bundle: dict) -> str:
    require("OPENAI_API_KEY")
    client = OpenAI(api_key=OPENAI_API_KEY)
    payload = json.dumps(bundle, ensure_ascii=False, indent=2, default=str)
    user = (
        f"종목: {bundle['name']} ({bundle['ticker']})\n"
        f"현재 tier: {bundle['score']['tier']}\n\n"
        f"데이터 bundle (JSON):\n{payload}\n\n"
        "위 데이터에 기반하여 12-section markdown deep dive 보고서를 작성해 주세요."
    )
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content


# ─────────────────────────────────────────────────────────────────────────────
# 5) Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker", help="종목코드 6자리 (예: 021820)")
    parser.add_argument("--year", type=int, default=datetime.now().year - 1,
                        help="DART 사업보고서 기준 연도 (default: 작년)")
    parser.add_argument("--output", default=None,
                        help="저장 파일 경로 (default: deep_dive_<ticker>.md)")
    parser.add_argument("--print", dest="to_stdout", action="store_true",
                        help="저장하지 않고 stdout 출력만")
    parser.add_argument("--context", default=None,
                        help="추가 historical context (배임 이력, 외부 사건 등). "
                             "LLM 의 거버넌스 섹션에 통합됨.")
    parser.add_argument("--context-file", default=None,
                        help="컨텍스트를 파일에서 읽기")
    args = parser.parse_args()

    extra_context = args.context
    if args.context_file:
        with open(args.context_file, encoding="utf-8") as f:
            extra_context = (extra_context or "") + "\n\n" + f.read()

    bundle = build_bundle(args.ticker, args.year, extra_context=extra_context)

    print(f"\n[{args.ticker}] OpenAI 호출 중 (모델: {OPENAI_MODEL}) ...")
    md = call_openai_deep_dive(bundle)

    header = (f"# Deep Dive — {bundle['name']} ({args.ticker})\n\n"
              f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
              f"tier: {bundle['score']['tier']} · model: {OPENAI_MODEL}*\n\n---\n\n")
    full = header + md

    if args.to_stdout:
        print("\n" + full)
        return

    out_path = Path(args.output) if args.output else (
        Path(__file__).resolve().parent / f"deep_dive_{args.ticker}.md")
    out_path.write_text(full, encoding="utf-8")
    print(f"\n저장: {out_path} ({len(full)} chars)")


if __name__ == "__main__":
    main()
