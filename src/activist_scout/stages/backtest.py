"""
행동주의 저PBR 백테스트 (전체 KOSPI 대상, 역사적 5%+ 공시 기반).

방법:
1. DART list.json(D001)으로 전 KOSPI 5%+ 공시 목록 수집 (분기별 페이지네이션)
2. list.json의 flr_nm(제출인)을 기준으로 classify_filer → activist / semi_activist 필터
3. pykrx get_market_fundamental_by_ticker → 공시 시점 PBR
4. pykrx get_market_ohlcv → 3/6/12/24/36M forward return
5. 연도별 요약 + 종목×펀드 상세 테이블 출력

결과:
  backtest_activist.json   — 전체 원본 데이터
  backtest_activist.md     — 연도별 리포트

사용법:
  python backtest_activist.py                        # 2021년 이후
  python backtest_activist.py --since-year 2019      # 2019년 이후
  python backtest_activist.py --max-pbr 1.0          # PBR 필터 완화
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock

from activist_scout.config import DART_VIEWER_URL, require
from activist_scout.domain import classify_filer
from activist_scout.utils import dart_get, fetch_with_retry

OUT_JSON = Path(__file__).resolve().parent / "backtest_activist.json"
OUT_MD   = Path(__file__).resolve().parent / "backtest_activist.md"

PERIODS  = [3, 6, 12, 24, 36]   # months


# ─────────────────────────────────────────────────────────────
# STEP 1 : DART 5%+ 공시 목록 수집
# ─────────────────────────────────────────────────────────────

def _quarter_ranges(since_year: int) -> list[tuple[str, str]]:
    """since_year 1Q ~ 현재까지 3개월 단위 날짜 쌍."""
    today = datetime.now()
    ranges = []
    y, q = since_year, 1
    while True:
        m_start = (q - 1) * 3 + 1
        m_end   = q * 3
        bgn = f"{y}{m_start:02d}01"
        if m_end == 12:
            end = f"{y}1231"
        else:
            import calendar
            last = calendar.monthrange(y, m_end)[1]
            end = f"{y}{m_end:02d}{last:02d}"
        ranges.append((bgn, end))
        if y > today.year or (y == today.year and m_end >= today.month):
            break
        q += 1
        if q > 4:
            q = 1
            y += 1
    return ranges


def fetch_filing_list(since_year: int) -> list[dict]:
    """DART list.json (D001, KOSPI) 분기별 페이지네이션으로 전체 수집."""
    quarters = _quarter_ranges(since_year)
    all_filings: list[dict] = []

    for bgn, end in quarters:
        page = 1
        while True:
            r = dart_get("list.json", {
                "corp_cls": "Y",
                "pblntf_detail_ty": "D001",
                "bgn_de": bgn,
                "end_de": end,
                "page_no": page,
                "page_count": 100,
            })
            if not r or r.get("status") != "000":
                break
            items = r.get("list", [])
            all_filings.extend(items)
            total = int(r.get("total_count", 0))
            if page * 100 >= total:
                break
            page += 1
            time.sleep(0.15)
        time.sleep(0.2)

    print(f"  공시 목록 수집: {len(all_filings)}건 ({since_year}~현재)")
    return all_filings


# ─────────────────────────────────────────────────────────────
# STEP 2 : activist 필터 + PBR + forward return
# ─────────────────────────────────────────────────────────────

_PBR_CACHE: dict[str, pd.DataFrame] = {}


def _get_pbr(date_str: str, ticker: str) -> float | None:
    if date_str not in _PBR_CACHE:
        try:
            df = stock.get_market_fundamental_by_ticker(date_str, market="KOSPI")
            if df is not None and not df.empty and "PBR" in df.columns:
                df.index = df.index.astype(str).str.zfill(6)
                _PBR_CACHE[date_str] = df[["PBR"]]
            else:
                _PBR_CACHE[date_str] = pd.DataFrame()
        except Exception:
            _PBR_CACHE[date_str] = pd.DataFrame()
    df = _PBR_CACHE[date_str]
    if df.empty or ticker not in df.index:
        return None
    v = float(df.loc[ticker, "PBR"])
    return v if v > 0 else None


_OHLCV_CACHE: dict[str, pd.DataFrame] = {}


def _stock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    key = f"{ticker}_{start}_{end}"
    if key not in _OHLCV_CACHE:
        df = fetch_with_retry(
            stock.get_market_ohlcv, start, end, ticker, retries=2, sleep=0.3
        )
        _OHLCV_CACHE[key] = df if (df is not None and not df.empty) else None
    return _OHLCV_CACHE[key]


# KOSPI 종합지수(1001) 캐시 — alpha 계산용 통제군
_KOSPI_INDEX: pd.DataFrame | None = None


def load_kospi_index(start: str, end: str) -> pd.DataFrame | None:
    """KOSPI 종합지수 OHLCV 한 번만 로드 후 캐시."""
    global _KOSPI_INDEX
    if _KOSPI_INDEX is None:
        df = fetch_with_retry(
            stock.get_index_ohlcv, start, end, "1001", retries=4, sleep=1.0
        )
        if df is None or df.empty or "종가" not in df.columns:
            return None
        _KOSPI_INDEX = df
    return _KOSPI_INDEX


def _ret_window(df: pd.DataFrame, base_dt: datetime, end_dt: datetime) -> float | None:
    if df is None or df.empty:
        return None
    sub = df[(df.index >= pd.Timestamp(base_dt)) & (df.index <= pd.Timestamp(end_dt))]
    if len(sub) < 2:
        return None
    p0, p1 = float(sub["종가"].iloc[0]), float(sub["종가"].iloc[-1])
    if p0 <= 0:
        return None
    return (p1 / p0 - 1) * 100


def fwd_ret_with_alpha(ticker: str, base_dt: datetime, months: int,
                       kospi: pd.DataFrame | None) -> tuple[float | None, float | None, float | None]:
    """(stock_ret, kospi_ret, alpha) %. alpha = stock − kospi (시장베타 제거)."""
    today = datetime.now()
    end_dt = base_dt + timedelta(days=months * 31)
    if end_dt > today:
        return None, None, None
    df = _stock_ohlcv(
        ticker,
        base_dt.strftime("%Y%m%d"),
        (base_dt + timedelta(days=months * 31 + 60)).strftime("%Y%m%d"),
    )
    sr = _ret_window(df, base_dt, end_dt)
    kr = _ret_window(kospi, base_dt, end_dt) if kospi is not None else None
    if sr is None:
        return None, kr, None
    if kr is None:
        return round(sr, 1), None, None
    return round(sr, 1), round(kr, 1), round(sr - kr, 1)


# 호환성: 기존 fwd_ret 시그니처 보존 (raw return만)
def fwd_ret(ticker: str, base_dt: datetime, months: int) -> float | None:
    sr, _, _ = fwd_ret_with_alpha(ticker, base_dt, months, None)
    return sr


# ─────────────────────────────────────────────────────────────
# STEP 3 : 집계 + 리포트
# ─────────────────────────────────────────────────────────────

def _stats(vals) -> dict:
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0}
    d = {
        "n":        len(v),
        "mean":     round(statistics.mean(v), 1),
        "median":   round(statistics.median(v), 1),
        "min":      round(min(v), 1),
        "max":      round(max(v), 1),
        "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 0),
    }
    if len(v) >= 4:
        qs = statistics.quantiles(v, n=4)
        d["p25"] = round(qs[0], 1)
        d["p75"] = round(qs[2], 1)
    return d


def build_markdown(events: list[dict], params: dict) -> str:
    lines = []
    lines.append("# 행동주의 저PBR 백테스트 결과\n")
    lines.append(f"> 조건: PBR ≤ {params['max_pbr']} · {params['since_year']}년 이후 · "
                 f"filer: activist / semi_activist  \n")
    lines.append(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")

    lines.append("## 구간별 raw 수익률\n")
    lines.append("| 구간 | n | 평균 | 중앙값 | min | max | 승률 | p25 | p75 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for m in PERIODS:
        s = _stats(e.get(f"ret_{m}M") for e in events)
        if s["n"] == 0:
            continue
        p25 = f"{s.get('p25','—')}%" if s.get('p25') is not None else "—"
        p75 = f"{s.get('p75','—')}%" if s.get('p75') is not None else "—"
        lines.append(
            f"| {m}M | {s['n']} | {s['mean']}% | {s['median']}% | "
            f"{s['min']}% | {s['max']}% | {s['win_rate']:.0f}% | {p25} | {p75} |"
        )
    lines.append("")

    lines.append("## 구간별 KOSPI 초과수익 (alpha)\n")
    lines.append("| 구간 | n | 평균 | 중앙값 | min | max | alpha-승률 | p25 | p75 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for m in PERIODS:
        s = _stats(e.get(f"alpha_{m}M") for e in events)
        if s["n"] == 0:
            continue
        p25 = f"{s.get('p25','—')}%" if s.get('p25') is not None else "—"
        p75 = f"{s.get('p75','—')}%" if s.get('p75') is not None else "—"
        lines.append(
            f"| {m}M | {s['n']} | {s['mean']}% | {s['median']}% | "
            f"{s['min']}% | {s['max']}% | {s['win_rate']:.0f}% | {p25} | {p75} |"
        )
    lines.append("")

    lines.append("## 펀드 유형별 12M 성과 (raw vs alpha)\n")
    lines.append("| 유형 | n | raw 평균 | raw 승률 | alpha 평균 | alpha 승률 |")
    lines.append("|---|---|---|---|---|---|")
    for ft in ("activist", "semi_activist"):
        sub = [e for e in events if e["filer_type"] == ft]
        sr = _stats(e.get("ret_12M") for e in sub)
        sa = _stats(e.get("alpha_12M") for e in sub)
        if sr["n"] == 0:
            continue
        a_mean = f"{sa.get('mean','—')}%" if sa["n"] else "—"
        a_win = f"{sa.get('win_rate','—'):.0f}%" if sa["n"] else "—"
        lines.append(
            f"| {ft} | {len(sub)} | {sr['mean']}% | {sr['win_rate']:.0f}% | {a_mean} | {a_win} |"
        )
    lines.append("")

    by_year: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        year = int(e["filing_date"][:4])
        by_year[year].append(e)

    for year in sorted(by_year.keys()):
        evs = sorted(by_year[year], key=lambda e: e.get("pbr_at_filing") or 99)
        lines.append(f"## {year}년 ({len(evs)}건)\n")
        lines.append("| 종목 | 펀드 | 공시일 | PBR | 3M | 6M | 12M | 24M | 36M |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for e in evs:
            def r(k):
                v = e.get(k)
                return f"{v:+.0f}%" if v is not None else "—"
            pbr = f"{e['pbr_at_filing']:.2f}" if e.get("pbr_at_filing") else "—"
            rcept = e.get("rcept_no", "")
            dart_url = DART_VIEWER_URL.format(rcept) if rcept else ""
            date_cell = f"[{e['filing_date']}]({dart_url})" if dart_url else e["filing_date"]
            lines.append(
                f"| {e['name']}({e['ticker']}) | {e['filer']} | {date_cell} | "
                f"{pbr} | "
                f"{r('ret_3M')} | {r('ret_6M')} | {r('ret_12M')} | {r('ret_24M')} | {r('ret_36M')} |"
            )
        s12 = _stats(e.get("ret_12M") for e in evs)
        if s12["n"] > 0:
            lines.append(f"\n> {year} 12M: 평균 **{s12['mean']}%** · 중앙값 {s12['median']}% · 승률 {s12['win_rate']:.0f}% (n={s12['n']})\n")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────

def main() -> None:
    require("KRX_ID", "KRX_PW", "DART_API_KEY")
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-year", type=int, default=2021)
    parser.add_argument("--max-pbr",    type=float, default=0.8)
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md",   default=str(OUT_MD))
    args = parser.parse_args()

    print("=" * 60)
    print(f"행동주의 저PBR 백테스트 (since {args.since_year}, PBR≤{args.max_pbr})")
    print("=" * 60)

    # 1) 공시 목록
    print("\n[1/3] DART 5%+ 공시 목록 수집 (list.json) ...")
    filings = fetch_filing_list(args.since_year)
    if not filings:
        raise SystemExit("공시 목록 없음")

    # 2) activist 필터 + PBR + return
    print(f"\n[2/3] 제출인(flr_nm) 필터링 및 수익률+alpha 계산 ...")
    # KOSPI 인덱스 prefetch (since_year 1월 ~ 오늘+버퍼)
    kospi_start = f"{args.since_year}0101"
    kospi_end = (datetime.now() + timedelta(days=10)).strftime("%Y%m%d")
    kospi = load_kospi_index(kospi_start, kospi_end)
    print(f"  KOSPI 종합 prefetch: {len(kospi) if kospi is not None else 0} 거래일")

    events: list[dict] = []
    done = 0

    for f in filings:
        sc = f.get("stock_code", "")
        if not sc or len(sc) != 6:
            continue

        flr = f.get("flr_nm", "")
        ftype = classify_filer(flr)
        if ftype not in ("activist", "semi_activist"):
            continue

        raw_dt = f.get("rcept_dt", "")
        if len(raw_dt) != 8 or not raw_dt.isdigit():
            continue
        if int(raw_dt[:4]) < args.since_year:
            continue

        filing_dt_str = f"{raw_dt[:4]}-{raw_dt[4:6]}-{raw_dt[6:8]}"
        filing_dt = datetime.strptime(filing_dt_str, "%Y-%m-%d")

        # PBR 조회
        pbr = _get_pbr(raw_dt, sc)
        if pbr is not None and pbr > args.max_pbr:
            continue

        ev = {
            "ticker":       sc,
            "name":         f.get("corp_name", ""),
            "filer":        flr,
            "filer_type":   ftype,
            "filing_date":  filing_dt_str,
            "rcept_no":     f.get("rcept_no", ""),
            "report_nm":    f.get("report_nm", ""),
            "pbr_at_filing": pbr,
        }

        for m in PERIODS:
            sr, kr, alpha = fwd_ret_with_alpha(sc, filing_dt, m, kospi)
            ev[f"ret_{m}M"] = sr
            ev[f"kospi_{m}M"] = kr
            ev[f"alpha_{m}M"] = alpha

        events.append(ev)

        done += 1
        if done % 50 == 0:
            print(f"  ... {done}건의 행동주의 공시 처리됨")

    print(f"  → 최종 이벤트(필터 통과): {len(events)}건")

    # 중복 제거: ticker × filer_type 최초 1건
    seen: dict[tuple, str] = {}
    for e in events:
        key = (e["ticker"], e["filer"], e["filer_type"])
        if key not in seen or e["filing_date"] < seen[key]:
            seen[key] = e["filing_date"]
    events_dedup = [e for e in events
                    if seen.get((e["ticker"], e["filer"], e["filer_type"])) == e["filing_date"]]
    events_dedup.sort(key=lambda e: e["filing_date"])
    print(f"  중복 제거 후: {len(events_dedup)}건")

    # 3) 저장
    print("\n[3/3] 저장 중 ...")
    summary = {}
    for m in PERIODS:
        summary[f"ret_{m}M"] = _stats(e.get(f"ret_{m}M") for e in events_dedup)
        summary[f"alpha_{m}M"] = _stats(e.get(f"alpha_{m}M") for e in events_dedup)

    out = {
        "params": {"since_year": args.since_year, "max_pbr": args.max_pbr},
        "total_events": len(events_dedup),
        "summary": summary,
        "events": events_dedup,
    }
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {args.out_json}")

    md = build_markdown(events_dedup, {"since_year": args.since_year, "max_pbr": args.max_pbr})
    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  MD:   {args.out_md}")

    # 콘솔 요약
    print("\n" + "=" * 60)
    print("구간별 raw 수익률 vs KOSPI 초과수익(alpha)")
    print("=" * 60)
    print(f"  {'구간':>5}  {'n':>4}  {'raw 평균':>9}  {'raw 승률':>9}  {'alpha 평균':>10}  {'alpha 승률':>10}")
    for m in PERIODS:
        sr = _stats(e.get(f"ret_{m}M") for e in events_dedup)
        sa = _stats(e.get(f"alpha_{m}M") for e in events_dedup)
        if sr["n"] == 0:
            continue
        rmean = sr.get("mean", 0)
        rwin = sr.get("win_rate", 0)
        amean = sa.get("mean", 0) if sa["n"] > 0 else None
        awin = sa.get("win_rate", 0) if sa["n"] > 0 else None
        amean_str = f"{amean:>9.1f}%" if amean is not None else "       —"
        awin_str = f"{awin:>9.0f}%" if awin is not None else "       —"
        print(f"  {m:>3}M   {sr['n']:>4}  {rmean:>8.1f}%  {rwin:>8.0f}%   {amean_str}   {awin_str}")


if __name__ == "__main__":
    main()
