"""
v5 — 3축 스코어 + tier 분류 (룰베이스, regime-agnostic).

이전 calibrate.py(Beta-binomial) 가 historical 데이터에 의존했던 문제를
post-amendment 상법 환경에 부적합 → first-principles 룰베이스로 대체.

3축:
  1) TARGET_ATTRACTIVENESS — 행동주의 펀드가 매력 느낄 만한가
  2) ACCUMULATION_SIGNATURE — 이미 매집 흔적이 있는가 (sub-5% 단계)
  3) LEGAL_VULNERABILITY    — post-amendment 상법 leverage가 큰가

Tier:
  HOT   — 3축 모두 ≥ axis_strong
  WARM  — 2축 ≥ axis_strong
  WATCH — 1축 ≥ axis_v_strong
  LATE  — 최근 12M 5%+ filing 이미 있음 (정보 우위 소진)
  AVOID — 최대주주 ≥ AVOID_OWNER_PCT 또는 명백한 록인

각 종목에 대해:
  - axes 상세 (어느 룰이 발동했는지) — 디버깅 / 검토 가능
  - tier
  - 진입 권고 (후속 모니터링 trigger 포함)

출력: scores.json + scores.csv
"""
from __future__ import annotations

import csv
import json
import statistics

import pandas as pd

from datetime import datetime

from pykrx import stock

from activist_scout.config import (
    ACCUM_WEIGHTS, AVOID_OWNER_PCT, AVOID_EXEMPT_NAV_DISCOUNT,
    AVOID_EXEMPT_TREASURY_SCORE, CAPTIVE_RELATED_PARTY_PCT, CAPTIVE_PARENT_STAKE_PCT,
    CATALYST_TIMING_DAYS, CLASSIFICATION_JSON, ENRICHED_JSON,
    FLOW_CSV, LATE_FILING_DAYS, LATE_THRESHOLDS, LEGAL_WEIGHTS,
    LIQUIDITY_CSV, REPORT_MD, SCORES_CSV, SCORES_JSON, SCREENING_CSV,
    TARGET_WEIGHTS, TIER_THRESHOLDS, require,
)
from activist_scout.utils import fetch_with_retry


# ─────────────────────────────────────────────────────────────────────────────
# Sanity check — yfinance 의심값 자동 flag
# ─────────────────────────────────────────────────────────────────────────────

def fundamentals_sanity_flags(f: dict) -> list[str]:
    """비정상 값 검출. 해당 항목은 score 계산에서 제외 + 리포트에 ⚠️ 표시.

    임계값 근거: 잉여자본비율 > 5.0 = 시총의 5배 순현금 = yfinance 잘못 계산 가능성 농후.
    실제 deep cash 종목 (예: 세원정공 1.9, 다우기술 15.4) 중 후자만 flag.
    """
    flags = []
    cap = f.get("잉여자본비율")
    if cap is not None and cap > 5.0:
        flags.append(f"⚠️ 잉여자본비율 {cap} (시총 대비 5배 초과 — yfinance 데이터 검증 필수)")
    if cap is not None and cap < -3.0:
        flags.append(f"⚠️ 잉여자본비율 {cap} (극단적 순부채 — 검증 필요)")
    pbr = f.get("PBR")
    if pbr is not None and (pbr < 0.05 or pbr > 5):
        flags.append(f"⚠️ PBR {pbr} (정상 범위 벗어남)")
    return flags


# ─────────────────────────────────────────────────────────────────────────────
# Axis 1 — TARGET ATTRACTIVENESS
# ─────────────────────────────────────────────────────────────────────────────

def score_target_attractiveness(stock_dict: dict, sector_pbr_median: float | None,
                                kospi_pbr_median: float,
                                sanity_flags: list[str],
                                nav: dict | None = None) -> tuple[int, list[str]]:
    """행동주의 펀드가 매력 느낄 만한가. sanity_flags 발동 종목은 의심 룰 제외."""
    score = 0
    triggered: list[str] = []
    f = stock_dict.get("fundamentals") or {}
    cap_suspicious = any("잉여자본비율" in flag for flag in sanity_flags)

    # 잉여자본 (deep_cash 우선, 둘 중 하나만) — sanity flag 시 제외
    cap = f.get("잉여자본비율")
    if cap is not None and not cap_suspicious:
        if cap >= 0.50:
            score += TARGET_WEIGHTS["deep_cash"]
            triggered.append(f"deep_cash ({cap:.2f}≥0.50)")
        elif cap >= 0.20:
            score += TARGET_WEIGHTS["moderate_cash"]
            triggered.append(f"moderate_cash ({cap:.2f}≥0.20)")

    # 오너 sweet spot
    owner = f.get("최대주주_지분율(%)")
    if owner is not None and 40 <= owner <= 55:
        score += TARGET_WEIGHTS["owner_sweet_spot"]
        triggered.append(f"owner_sweet_spot ({owner:.1f}%)")

    # PBR vs sector median
    pbr = f.get("PBR")
    target = sector_pbr_median if (sector_pbr_median and sector_pbr_median > 0) else kospi_pbr_median
    if pbr and target and pbr <= target * 0.7:
        score += TARGET_WEIGHTS["pbr_gap_30pct"]
        triggered.append(f"pbr_gap_30pct (PBR {pbr:.2f} ≤ {target:.2f}×0.7)")

    # 시총 capacity
    mcap_eok = f.get("시가총액(억)")
    if mcap_eok is not None:
        if mcap_eok >= 2000:
            score += TARGET_WEIGHTS["cap_large"]
            triggered.append(f"cap_large ({mcap_eok:.0f}억)")
        elif mcap_eok >= 500:
            score += TARGET_WEIGHTS["cap_mid"]
            triggered.append(f"cap_mid ({mcap_eok:.0f}억)")

    # 자사주 보유 (treasury_score 양수면 proxy로 가산)
    summary = stock_dict.get("summary") or {}
    if (summary.get("treasury_score") or 0) > 0:
        score += TARGET_WEIGHTS["self_treasury_5pct"]
        triggered.append(f"treasury_friendly_proxy (score={summary.get('treasury_score')})")

    # NAV 디스카운트 (sum-of-parts) — 지주사 deep value 핵심 시그널
    # P3: trust score 가 LOW 이고 premium 시그널이면 무시. 디스카운트는 LOW 에서도 인정 (보수적)
    if nav and nav.get("discount_pct") is not None and nav.get("trustworthy_signal", True):
        d = nav["discount_pct"]
        trust = nav.get("trust", "?")
        if d <= -50:
            score += TARGET_WEIGHTS["nav_discount_50"]
            triggered.append(
                f"nav_discount_50 (시총-NAV gap {d:.0f}%, NAV {nav['total_nav_eok']:.0f}억, trust={trust})")
        elif d <= -30:
            score += TARGET_WEIGHTS["nav_discount_30"]
            triggered.append(
                f"nav_discount_30 ({d:.0f}%, NAV {nav['total_nav_eok']:.0f}억, trust={trust})")

    return min(score, 100), triggered


# ─────────────────────────────────────────────────────────────────────────────
# Axis 2 — ACCUMULATION SIGNATURE
# ─────────────────────────────────────────────────────────────────────────────

def _parse_buy_ratio(ratio_str: str) -> float:
    """'54/86' → 0.628"""
    try:
        num, den = ratio_str.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except Exception:
        return 0.0


def score_accumulation(flow: dict, liq: dict, mcap_eok: float | None) -> tuple[int, list[str]]:
    """이미 매집 흔적이 있는가."""
    score = 0
    triggered: list[str] = []

    # 90D 매수일 비율
    r90 = flow.get("순매수일/총_90D", "")
    ratio_90 = _parse_buy_ratio(r90) if r90 else 0
    if ratio_90 >= 0.65:
        score += ACCUM_WEIGHTS["buy_days_65pct"]
        triggered.append(f"buy_days_65pct (90D {ratio_90:.2f})")

    # 90D 누적 net buy / 시총
    net_buy_eok = flow.get("기관순매수_90D(억)")
    net_buy_ratio = None
    if net_buy_eok is not None and mcap_eok and mcap_eok > 0:
        net_buy_ratio = float(net_buy_eok) / float(mcap_eok)
        if net_buy_ratio >= 0.03:
            score += ACCUM_WEIGHTS["net_buy_3pct"]
            triggered.append(f"net_buy_3pct (90D {net_buy_ratio*100:.1f}% of mcap)")

    # 가격 통제된 매집 (vs 매수VWAP 가까움)
    vwap_gap = flow.get("vs_매수VWAP_90D(%)")
    if vwap_gap is not None and abs(float(vwap_gap)) <= 5:
        score += ACCUM_WEIGHTS["vwap_near"]
        triggered.append(f"vwap_near (gap {vwap_gap}%)")

    # 20D 매수일 지속
    r20 = flow.get("순매수일/총_20D", "")
    ratio_20 = _parse_buy_ratio(r20) if r20 else 0
    if ratio_20 >= 0.50:
        score += ACCUM_WEIGHTS["buy_days_50_short"]
        triggered.append(f"buy_days_50_short (20D {ratio_20:.2f})")

    # capacity
    cap_score = liq.get("capacity_score")
    if cap_score is not None and float(cap_score) >= 0.7:
        score += ACCUM_WEIGHTS["high_capacity"]
        triggered.append(f"high_capacity ({cap_score})")

    # 비밀유지(stake_secrecy) 보너스 — 일평균 매수액 / ADV 가 낮을수록 매집 노출 위험 낮음
    # 일평균 매수액(억) ≈ 90D 누적 net buy / 약 60 거래일 (= 90 calendar days × ~2/3)
    if (net_buy_eok is not None and net_buy_eok > 0
            and liq.get("ADV_20D(억)") is not None and liq["ADV_20D(억)"] > 0):
        daily_buy_avg = float(net_buy_eok) / 60
        adv = float(liq["ADV_20D(억)"])
        secrecy_ratio = daily_buy_avg / adv      # 작을수록 비밀 매집 가능
        if secrecy_ratio <= 0.05:                 # 일평균 매수가 ADV의 5% 미만 → 보이지 않게 매집
            score += ACCUM_WEIGHTS.get("stake_secrecy", 10)
            triggered.append(f"stake_secrecy (daily_buy/ADV={secrecy_ratio:.3f}≤0.05)")

    return min(score, 100), triggered


# ─────────────────────────────────────────────────────────────────────────────
# Axis 3 — LEGAL VULNERABILITY (post-amendment 상법 leverage)
# ─────────────────────────────────────────────────────────────────────────────

def _related_party_proxy(stock_dict: dict) -> tuple[int, str | None]:
    """일감몰아주기 v1 proxy — 사업보고서 §X 텍스트 파싱 미구현 상태에서의 약한 시그널.

    근거:
    - 같은 회사명 prefix (예: "사조") 가진 자회사 ≥ 3개 → 그룹 내부 거래 채널 多
    - 100% 보유 비상장 자회사 ≥ 5개 → 자본 이동 통로 다수 (불투명)
    - 둘 다 satisfy 시 일감몰아주기 의심 후보 (REVIEW flag)
    """
    name = stock_dict.get("name", "")
    subs = stock_dict.get("subsidiaries") or []
    if not subs or len(name) < 2:
        return 0, None

    # 본사명 처음 2자를 prefix로 (예: "사조산업" → "사조")
    prefix = name[:2]
    same_group = sum(1 for s in subs if (s.get("name") or "").startswith(prefix))

    full_owned_unlisted = sum(
        1 for s in subs
        if float(s.get("stake_pct") or 0) >= 95
        and float(s.get("book_value_won") or 0) > 0
    )

    if same_group >= 3 and full_owned_unlisted >= 5:
        return LEGAL_WEIGHTS["related_party"], (
            f"related_party_review (그룹내 자회사 {same_group}, 100%보유 비상장 {full_owned_unlisted}) "
            f"⚠️ 사업보고서 §X 직접 검증 권고"
        )
    elif same_group >= 5:
        return LEGAL_WEIGHTS["related_party"] // 2, (
            f"related_party_partial (그룹내 자회사 {same_group}, 본사명 prefix 매칭) "
            f"⚠️ 추가 review 권고"
        )
    return 0, None


def score_legal_vulnerability(stock_dict: dict) -> tuple[int, list[str]]:
    score = 0
    triggered: list[str] = []
    summary = stock_dict.get("summary") or {}

    # 자사주 처분 12M (treasury_dir_count 분석)
    dir_cnt = summary.get("treasury_dir_count") or {}
    if dir_cnt.get("dispose_planned", 0) + dir_cnt.get("dispose_done", 0) >= 1:
        score += LEGAL_WEIGHTS["treasury_dispose"]
        triggered.append(f"treasury_dispose ({dir_cnt.get('dispose_planned',0)+dir_cnt.get('dispose_done',0)}회)")

    if dir_cnt.get("trust_cancel", 0) >= 1:
        score += LEGAL_WEIGHTS["trust_cancel"]
        triggered.append(f"trust_cancel ({dir_cnt.get('trust_cancel')}회)")

    # 분할/합병 24M
    gov = summary.get("governance_count") or {}
    if gov.get("split_merge_24M", 0) >= 1:
        score += LEGAL_WEIGHTS["split_filing"]
        triggered.append(f"split_filing ({gov.get('split_merge_24M')}건 24M)")

    # 거래정지/회계 한정 5Y
    if gov.get("incident_5Y", 0) >= 1:
        score += LEGAL_WEIGHTS["audit_or_halt"]
        triggered.append(f"audit_or_halt ({gov.get('incident_5Y')}건 5Y)")

    # 임원 변동 빈도 (raw 변동 건수가 ≥ 8 이면 비정상)
    exec_count = len(stock_dict.get("exec_holdings", []) or [])
    if exec_count >= 8:
        score += LEGAL_WEIGHTS["exec_churn"]
        triggered.append(f"exec_churn ({exec_count}건)")

    # P1 v7: 사외이사 임기 만료 ≤ CATALYST_TIMING_DAYS → catalyst 임박
    tenure = stock_dict.get("exec_tenure") or []
    today = datetime.now().date()
    upcoming = []
    for t in tenure:
        if "사외" not in (t.get("rgist_at") or ""):
            continue
        end = t.get("tenure_end")
        if not end:
            continue
        try:
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
            days = (end_d - today).days
            if 0 <= days <= CATALYST_TIMING_DAYS:
                upcoming.append((days, t.get("name"), end))
        except Exception:
            pass
    if upcoming:
        upcoming.sort()
        d, nm, end = upcoming[0]
        score += LEGAL_WEIGHTS["outside_director_expiry"]
        triggered.append(
            f"outside_director_expiry (사외이사 {nm} 임기 D-{d}, {end}, "
            f"총 {len(upcoming)}명 ≤ {CATALYST_TIMING_DAYS}일)"
        )

    # 일감몰아주기 — LLM 분석 결과 우선, 없으면 structural proxy fallback
    rpa = stock_dict.get("related_party_analysis") or {}
    ratio = rpa.get("ratio_pct")
    confidence = rpa.get("confidence", "")
    if ratio is not None and confidence in ("high", "medium") and ratio >= 10:
        score += LEGAL_WEIGHTS["related_party"]
        triggered.append(
            f"related_party_quantified ({ratio:.1f}% [{confidence}], "
            f"매출 {(rpa.get('related_party_sales_won') or 0)/1e8:.0f}억)"
        )
    elif ratio is not None and confidence in ("high", "medium") and ratio >= 5:
        # 5~10% 구간은 약한 시그널
        score += LEGAL_WEIGHTS["related_party"] // 2
        triggered.append(f"related_party_partial ({ratio:.1f}% [{confidence}])")
    else:
        # LLM 분석 없거나 low confidence → proxy fallback
        rp_score, rp_msg = _related_party_proxy(stock_dict)
        if rp_score:
            score += rp_score
            triggered.append(rp_msg)

    return min(score, 100), triggered


# ─────────────────────────────────────────────────────────────────────────────
# LATE sub-tier — filing 후 KOSPI alpha 기준
# ─────────────────────────────────────────────────────────────────────────────

import pandas as pd

_KOSPI_CACHE: pd.DataFrame | None = None


def _load_kospi_full(lookback_days: int = 800):
    """KOSPI 종합지수(1001) 충분히 긴 range 한 번만 fetch.

    버그 방지: range 별 캐시 X. 가장 긴 range 한 번 fetch 후 종목별로 slicing.
    """
    global _KOSPI_CACHE
    if _KOSPI_CACHE is not None:
        return _KOSPI_CACHE
    from datetime import timedelta
    end = datetime.now()
    start = end - timedelta(days=lookback_days)
    df = fetch_with_retry(
        stock.get_index_ohlcv,
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001",
        retries=4, sleep=1.0,
    )
    _KOSPI_CACHE = df
    return df


def _sector_avg_return(filing_dt: datetime, today: datetime,
                       sector_tickers: list[str]) -> float | None:
    """동일 섹터 종목들의 같은 기간 평균 return (%). KOSPI 대신 통제군으로 사용."""
    if not sector_tickers or len(sector_tickers) < 2:
        return None
    rets = []
    start = filing_dt.strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    for tk in sector_tickers[:10]:        # 섹터 내 max 10개로 제한
        df = fetch_with_retry(stock.get_market_ohlcv, start, end, tk,
                              retries=1, sleep=0.3)
        if df is None or df.empty or "종가" not in df.columns:
            continue
        p0 = float(df["종가"].iloc[0])
        p1 = float(df["종가"].iloc[-1])
        if p0 > 0:
            rets.append((p1 / p0 - 1) * 100)
    if not rets:
        return None
    return round(sum(rets) / len(rets), 1)


def post_filing_alpha(ticker: str, filing_date: str,
                      sector_tickers: list[str] | None = None) -> dict | None:
    """가장 최근 행동주의 5%+ filing 이후 KOSPI + 섹터 alpha (%).

    sector_tickers 가 주어지면 섹터 평균 통제군도 계산 (alpha_sector_pct).
    Bull market dilution 보정용.
    """
    if not filing_date or len(filing_date) < 10:
        return None
    try:
        filing_dt = datetime.strptime(filing_date[:10], "%Y-%m-%d")
    except Exception:
        return None
    today = datetime.now()
    days_since = (today - filing_dt).days
    if days_since < 7:
        return None

    start = filing_dt.strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    stock_df = fetch_with_retry(stock.get_market_ohlcv, start, end, ticker,
                                retries=2, sleep=0.5)
    if stock_df is None or stock_df.empty or "종가" not in stock_df.columns:
        return None

    kospi_full = _load_kospi_full()
    if kospi_full is None or kospi_full.empty:
        return None
    s_start, s_end = stock_df.index[0], stock_df.index[-1]
    kospi_slice = kospi_full[(kospi_full.index >= s_start) & (kospi_full.index <= s_end)]
    if kospi_slice.empty:
        return None

    s0, s1 = float(stock_df["종가"].iloc[0]), float(stock_df["종가"].iloc[-1])
    k0, k1 = float(kospi_slice["종가"].iloc[0]), float(kospi_slice["종가"].iloc[-1])
    if s0 <= 0 or k0 <= 0:
        return None

    stock_ret = (s1 / s0 - 1) * 100
    kospi_ret = (k1 / k0 - 1) * 100
    alpha = stock_ret - kospi_ret
    out = {
        "filing_date": filing_date[:10],
        "days_since": days_since,
        "stock_return_pct": round(stock_ret, 1),
        "kospi_return_pct": round(kospi_ret, 1),
        "alpha_pct": round(alpha, 1),
    }

    # 섹터 통제 (Bull market sector beta 제거용)
    if sector_tickers:
        peer_avg = _sector_avg_return(filing_dt, today, sector_tickers)
        if peer_avg is not None:
            out["sector_avg_return_pct"] = peer_avg
            out["alpha_sector_pct"] = round(stock_ret - peer_avg, 1)

    return out


def late_sub_tier(alpha_kospi: float | None,
                  alpha_sector: float | None = None) -> str:
    """filing 후 alpha 기준 sub-classify.

    우선 sector alpha 사용 (bull market sector-beta 제거). 없으면 KOSPI alpha.
    PRICED_IN  : alpha ≥ +20%  → 시장 이미 반영
    SKEPTICAL  : alpha < −5%   → 시장 회의적, 역설적 매수 기회
    ACCESSIBLE : 그 사이        → 시장 미온적, 잔여 정보 차익
    """
    a = alpha_sector if alpha_sector is not None else alpha_kospi
    if a is None:
        return "LATE"
    if a >= LATE_THRESHOLDS["priced_in_alpha"]:
        return "LATE_PRICED_IN"
    if a < LATE_THRESHOLDS["skeptical_alpha"]:
        return "LATE_SKEPTICAL"
    return "LATE_ACCESSIBLE"


# ─────────────────────────────────────────────────────────────────────────────
# Tier 분류
# ─────────────────────────────────────────────────────────────────────────────

def classify_tier(target: int, accum: int, legal: int,
                  late_alpha_kospi: float | None,
                  late_alpha_sector: float | None,
                  late_flag: bool, avoid: bool) -> str:
    if avoid:
        return "AVOID"
    if late_flag:
        return late_sub_tier(late_alpha_kospi, late_alpha_sector)
    strong = TIER_THRESHOLDS["axis_strong"]
    v_strong = TIER_THRESHOLDS["axis_v_strong"]
    n_strong = sum(1 for s in (target, accum, legal) if s >= strong)
    n_v_strong = sum(1 for s in (target, accum, legal) if s >= v_strong)

    if n_strong >= 3:
        return "HOT"
    if n_strong >= 2:
        return "WARM"
    if n_v_strong >= 1:
        return "WATCH"
    return "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# Sector PBR median (universe 내부 + KOSPI 전체)
# ─────────────────────────────────────────────────────────────────────────────

def _ksic_2digit(induty) -> str | None:
    if induty is None:
        return None
    s = str(induty)
    return s[:2] if len(s) >= 2 else None


def compute_sector_medians(enr: dict) -> tuple[dict, float]:
    """Universe 내 induty 2자리 그룹별 median PBR + KOSPI 전체 median.

    `calibrate.py 의 compute_sector_pbr` 로직을 단순화해 가져옴.
    """
    by_sector: dict[str, list[float]] = {}
    for d in enr.values():
        induty = (d.get("company") or {}).get("induty_code")
        sec = _ksic_2digit(induty) or "??"
        pbr = (d.get("fundamentals") or {}).get("PBR")
        if pbr and pbr > 0:
            by_sector.setdefault(sec, []).append(float(pbr))

    medians = {sec: round(statistics.median(v), 3) for sec, v in by_sector.items() if v}
    overall = statistics.median([p for vs in by_sector.values() for p in vs])
    return medians, round(overall, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def most_recent_activist_filing(d: dict) -> str | None:
    """summary.recent_activists 에서 가장 최근 filing 의 date 추출."""
    summary = d.get("summary") or {}
    rec = summary.get("recent_activists") or []
    if not rec:
        return None
    dates = [r.get("date") for r in rec if r.get("date")]
    if not dates:
        return None
    return max(dates)   # ISO 형식이라 max 가 가장 최근


def _build_sector_ticker_map(enr: dict) -> dict[str, list[str]]:
    """induty 2자리 → ticker 리스트. 섹터 통제군 산출용."""
    out: dict[str, list[str]] = {}
    for tk, d in enr.items():
        sec = _ksic_2digit((d.get("company") or {}).get("induty_code"))
        if sec:
            out.setdefault(sec, []).append(tk)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Sum-of-parts NAV — 자회사 시총 기반
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_subsidiary_name(name: str) -> str:
    """매칭용 정규화 — 회사 형태 표기, 공백 제거."""
    if not name:
        return ""
    s = str(name)
    for token in ("주식회사", "(주)", "㈜", "(유)", "㈜", "Co.,Ltd", "Inc.", "Co."):
        s = s.replace(token, "")
    return s.replace(" ", "").strip()


def _load_corp_to_stock_index() -> dict[str, str]:
    """corp_code_map.json 의 corp_name → stock_code 역인덱스 (정규화 이름 기준)."""
    from activist_scout.utils import load_corp_map
    cm = load_corp_map()
    out = {}
    for stock_code, info in cm.items():
        nm = _normalize_subsidiary_name(info.get("corp_name", ""))
        if nm and nm not in out:
            out[nm] = stock_code
    return out


def _get_market_cap_eok(stock_code: str, base_date: str | None = None) -> float | None:
    """KOSPI 종목의 현재 시가총액(억). 분기마다 캐시."""
    try:
        from datetime import datetime
        d = base_date or datetime.now().strftime("%Y%m%d")
        df = fetch_with_retry(stock.get_market_cap, d, retries=2, sleep=0.3)
        if df is None or df.empty or stock_code not in df.index:
            return None
        return float(df.loc[stock_code, "시가총액"]) / 1e8
    except Exception:
        return None


def compute_nav_metrics(parent_mcap_eok: float | None, subsidiaries: list[dict],
                       corp_to_stock: dict[str, str],
                       cap_cache: dict[str, float]) -> dict | None:
    """Sum-of-parts NAV 계산 + trust score (P3).

    listed 자회사: 시총 × 보유지분 = NAV 기여
    unlisted 자회사: 장부가 (보수적)

    trust score (P3 v7):
      HIGH   — 상장 자회사 NAV 비중 ≥ 50% (시총 비교 시그널 신뢰)
      MEDIUM — 25~50%
      LOW    — < 25% (premium 시그널은 무시, 디스카운트만 trust)

    Returns:
        {
          "listed_count": ..., "unlisted_count": ...,
          "listed_nav_eok": ..., "unlisted_book_eok": ...,
          "total_nav_eok": ..., "listed_share_pct": ...,
          "trust": "HIGH/MEDIUM/LOW",
          "discount_pct": ..., "trustworthy_signal": True/False,
        }
    """
    if not subsidiaries or not parent_mcap_eok:
        return None
    listed_nav = 0.0
    unlisted_nav = 0.0
    listed_count = 0
    unlisted_count = 0
    matched: list[dict] = []

    for s in subsidiaries:
        nm = _normalize_subsidiary_name(s.get("name", ""))
        stake = float(s.get("stake_pct") or 0) / 100
        book_won = float(s.get("book_value_won") or 0)
        if stake <= 0:
            continue

        sub_stock = corp_to_stock.get(nm)
        if sub_stock:
            mcap_eok = cap_cache.get(sub_stock)
            if mcap_eok is None:
                mcap_eok = _get_market_cap_eok(sub_stock)
                cap_cache[sub_stock] = mcap_eok if mcap_eok else 0
            if mcap_eok and mcap_eok > 0:
                contribution = mcap_eok * stake
                listed_nav += contribution
                listed_count += 1
                matched.append({
                    "name": s.get("name"), "stock_code": sub_stock,
                    "stake_pct": s.get("stake_pct"),
                    "subsidiary_mcap_eok": round(mcap_eok, 0),
                    "nav_contribution_eok": round(contribution, 0),
                    "type": "listed",
                })
                continue
        # unlisted: book value
        unlisted_nav += book_won / 1e8
        unlisted_count += 1
        matched.append({
            "name": s.get("name"), "stock_code": None,
            "stake_pct": s.get("stake_pct"),
            "book_value_eok": round(book_won / 1e8, 0),
            "type": "unlisted",
        })

    total_nav = listed_nav + unlisted_nav
    if total_nav <= 0:
        return None
    discount = (parent_mcap_eok - total_nav) / total_nav * 100

    # P3: trust score — 상장 자회사 NAV 비중 기반
    listed_share = listed_nav / total_nav if total_nav > 0 else 0
    if listed_share >= 0.50:
        trust = "HIGH"
    elif listed_share >= 0.25:
        trust = "MEDIUM"
    else:
        trust = "LOW"

    # 시그널 신뢰도: 디스카운트(음수)는 LOW에서도 trust, premium(양수)은 HIGH/MEDIUM에서만 trust
    if discount <= 0:
        trustworthy_signal = True       # 시총 < NAV 는 비상장 underestimate에 *반대*되는 강한 시그널
    else:
        trustworthy_signal = (trust in ("HIGH", "MEDIUM"))

    return {
        "listed_count": listed_count,
        "unlisted_count": unlisted_count,
        "listed_nav_eok": round(listed_nav, 0),
        "unlisted_book_eok": round(unlisted_nav, 0),
        "total_nav_eok": round(total_nav, 0),
        "parent_mcap_eok": round(parent_mcap_eok, 0),
        "discount_pct": round(discount, 1),
        "listed_share_pct": round(listed_share * 100, 1),
        "trust": trust,
        "trustworthy_signal": trustworthy_signal,
        "matched_top": sorted(matched, key=lambda x: -(x.get("nav_contribution_eok") or
                                                       x.get("book_value_eok") or 0))[:10],
    }


def main():
    require("KRX_ID", "KRX_PW")
    enr = json.load(open(ENRICHED_JSON, encoding="utf-8"))
    sector_medians, overall_median = compute_sector_medians(enr)
    sector_tickers = _build_sector_ticker_map(enr)
    print(f"섹터 PBR median: {len(sector_medians)} 그룹, 전체 median {overall_median}")

    # NAV 계산용: 자회사 이름 → stock_code 인덱스 + 시총 캐시
    print("자회사 → 종목코드 인덱스 빌드 ...")
    corp_to_stock = _load_corp_to_stock_index()
    cap_cache: dict[str, float] = {}

    # classification.json 의 narrative 도 활용 (LLM의 정성)
    clf = {}
    try:
        clf_list = json.load(open(CLASSIFICATION_JSON, encoding="utf-8"))
        clf = {r["ticker"]: r for r in clf_list}
    except Exception:
        pass

    # liquidity.csv 로드
    liq_df = pd.read_csv(LIQUIDITY_CSV, dtype={"ticker": str})
    liq_map = liq_df.set_index("ticker").to_dict("index")

    rows = []
    for tk, d in enr.items():
        f = d.get("fundamentals") or {}
        flow = d.get("flow") or {}
        liq = liq_map.get(tk) or {}
        co = d.get("company") or {}
        sec = _ksic_2digit(co.get("induty_code"))
        sec_median = sector_medians.get(sec)

        sanity = fundamentals_sanity_flags(f)

        # NAV 계산 — subsidiaries 데이터 있을 때만
        nav = None
        subs = d.get("subsidiaries") or []
        if subs:
            nav = compute_nav_metrics(
                f.get("시가총액(억)"), subs, corp_to_stock, cap_cache)

        target_score, target_hits = score_target_attractiveness(
            d, sec_median, overall_median, sanity, nav)
        accum_score, accum_hits = score_accumulation(flow, liq, f.get("시가총액(억)"))
        legal_score, legal_hits = score_legal_vulnerability(d)

        # late + post-filing alpha (sector 통제 포함)
        recent_act = (d.get("summary") or {}).get("recent_activist_filings_12M", 0)
        late = recent_act > 0
        post_filing = None
        if late:
            filing_dt = most_recent_activist_filing(d)
            if filing_dt:
                peer_tickers = [t for t in (sector_tickers.get(sec) or []) if t != tk]
                post_filing = post_filing_alpha(tk, filing_dt, peer_tickers)

        # AVOID 룰 — 우선순위:
        # 1) Captive subsidiary (모회사 의무거래로 해체 동기 X)
        # 2) 최대주주 ≥ 65% (행동주의 무력화)
        # 3) 60~65% + 면제 조건 미충족
        owner = f.get("최대주주_지분율(%)") or 0
        nav_discount = (nav or {}).get("discount_pct") if nav else None
        ts_score = (d.get("summary") or {}).get("treasury_score") or 0
        rpa = d.get("related_party_analysis") or {}
        rp_ratio = rpa.get("ratio_pct") if rpa else None
        avoid = False
        avoid_reason = None

        # P2 — Captive 식별
        if rp_ratio is not None and rp_ratio >= CAPTIVE_RELATED_PARTY_PCT:
            # 일감 비중 ≥ 50% → captive 의무거래 의심
            # 추가 confirmation: 최대주주가 strategic (법인) AND ≥ 50%
            holdings = d.get("major_holdings_5pct") or []
            strategic_parent = any(
                h.get("filer_type") == "strategic"
                and (float(h.get("stkrt") or 0) >= CAPTIVE_PARENT_STAKE_PCT)
                for h in holdings
            )
            if strategic_parent or owner >= CAPTIVE_PARENT_STAKE_PCT:
                avoid = True
                avoid_reason = (f"CAPTIVE: 일감몰아주기 {rp_ratio:.0f}% ≥ {CAPTIVE_RELATED_PARTY_PCT:.0f}% "
                                f"+ 모회사 지분 ≥ {CAPTIVE_PARENT_STAKE_PCT:.0f}% — 의무거래로 행동주의 무력")

        if not avoid:
            if owner >= AVOID_OWNER_PCT:
                avoid = True
                avoid_reason = f"최대주주 {owner:.1f}% ≥ {AVOID_OWNER_PCT}%"
            elif owner >= 60.0:
                # 60~65%: 면제 조건 체크
                exempt = []
                if nav_discount is not None and nav_discount <= AVOID_EXEMPT_NAV_DISCOUNT:
                    exempt.append(f"NAV 디스카운트 {nav_discount:+.1f}% ≤ {AVOID_EXEMPT_NAV_DISCOUNT}")
                if ts_score >= AVOID_EXEMPT_TREASURY_SCORE:
                    exempt.append(f"treasury_score {ts_score:+.1f} ≥ {AVOID_EXEMPT_TREASURY_SCORE}")
                if not exempt:
                    avoid = True
                    avoid_reason = f"최대주주 {owner:.1f}% ∈ [60, 65) 면제 조건 미충족"
                else:
                    avoid_reason = f"최대주주 {owner:.1f}% but 면제: {', '.join(exempt)}"

        late_alpha_kospi = post_filing.get("alpha_pct") if post_filing else None
        late_alpha_sector = post_filing.get("alpha_sector_pct") if post_filing else None
        tier = classify_tier(target_score, accum_score, legal_score,
                             late_alpha_kospi, late_alpha_sector, late, avoid)

        narrative = (clf.get(tk) or {}).get("narrative", "")
        primary_trap = (clf.get(tk) or {}).get("primary_trap_type", "")

        rows.append({
            "ticker": tk,
            "name": d["name"],
            "tier": tier,
            "target_score": target_score,
            "accum_score": accum_score,
            "legal_score": legal_score,
            "total_score": target_score + accum_score + legal_score,
            "target_hits": target_hits,
            "accum_hits": accum_hits,
            "legal_hits": legal_hits,
            "PBR": f.get("PBR"),
            "최대주주_지분율(%)": owner,
            "잉여자본비율": f.get("잉여자본비율"),
            "시가총액(억)": f.get("시가총액(억)"),
            "현재가": flow.get("현재가"),
            "기관순매수_90D(억)": flow.get("기관순매수_90D(억)"),
            "capacity_score": liq.get("capacity_score"),
            "sector_ksic2": sec,
            "sector_median_PBR": sec_median,
            "kospi_median_PBR": overall_median,
            "recent_activist_12M": recent_act,
            "post_filing": post_filing,           # filing 후 alpha (LATE 만)
            "nav": nav,                            # sum-of-parts (지주사 등)
            "sanity_flags": sanity,
            "avoid_reason": avoid_reason,         # 면제 또는 차단 사유 추적
            "primary_trap_type": primary_trap,
            "narrative": narrative,
        })

    # 정렬 우선순위 — frontrun 후보 → LATE 매력 순 → 기타
    # LATE_SKEPTICAL은 "정보 공개됐는데 시장이 회의적" → 매력적이라 WATCH 위로 올림
    tier_order = {
        "HOT": 0, "WARM": 1, "LATE_SKEPTICAL": 2, "WATCH": 3,
        "LATE_ACCESSIBLE": 4, "LATE_PRICED_IN": 5, "LATE": 6,
        "PASS": 7, "AVOID": 8,
    }
    rows.sort(key=lambda r: (tier_order.get(r["tier"], 9), -r["total_score"]))

    # JSON 저장
    with open(SCORES_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "sector_medians": sector_medians,
            "kospi_median_PBR": overall_median,
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"저장: {SCORES_JSON}")

    # CSV 저장 (간략)
    fields = ["ticker", "name", "tier", "target_score", "accum_score", "legal_score",
              "total_score", "PBR", "최대주주_지분율(%)", "잉여자본비율",
              "시가총액(억)", "현재가", "기관순매수_90D(억)", "capacity_score",
              "recent_activist_12M", "primary_trap_type"]
    with open(SCORES_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fields})
    print(f"저장: {SCORES_CSV}")

    # tier 분포
    from collections import Counter
    print(f"\n=== Tier 분포 ===")
    for tier, cnt in Counter(r["tier"] for r in rows).most_common():
        print(f"  {tier}: {cnt}")

    # Top 10
    print(f"\n=== Top 10 (tier asc, total_score desc) ===")
    for r in rows[:10]:
        print(f"  {r['ticker']} {r['name']:14s} | {r['tier']:6s} | "
              f"T={r['target_score']:>3} A={r['accum_score']:>3} L={r['legal_score']:>3} | "
              f"PBR={r.get('PBR','?')} 최대주주={r.get('최대주주_지분율(%)','?')}% "
              f"잉여자본={r.get('잉여자본비율','?')}")


if __name__ == "__main__":
    main()
