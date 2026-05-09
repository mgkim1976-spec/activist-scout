"""
과거 리레이팅 사례 발굴 (historical re-rating scanner).

전략:
1. pykrx로 KOSPI 전체 종목의 연말 PBR 스냅샷 수집 (since_year ~ today-2년)
2. 특정 연말에 PBR ≤ max_pbr 이었던 종목 추출
3. 이후 horizon_years 안에 PBR ≥ rerate_pbr 에 도달했는지 확인
4. DART enriched.json의 5%+ 공시와 교차 → activism 유무 표시
5. 해당 구간 주가 수익률 계산

결과: rerate_history.json + 콘솔 요약

사용법:
  python scan_rerate.py                         # 기본값
  python scan_rerate.py --since-year 2019 --max-pbr 0.7 --horizon 3
  python scan_rerate.py --tickers 005720,012630  # 특정 종목만

주의:
- pykrx 연말 PBR은 KRX 데이터 기준 (배당 미조정)
- KOSPI 전체 스캔 시 시간 소요 (~30분). 소형주 일부 누락 가능.
- 결과는 참고용 sniff test. 정확한 재무 분석은 별도 필요.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
from pykrx import stock

from config import ENRICHED_JSON, require
from utils import fetch_with_retry

RERATE_JSON = Path(__file__).resolve().parent / "rerate_history.json"


# ─────────────────────────────────────────────────────────────
# PBR 스냅샷
# ─────────────────────────────────────────────────────────────

def _year_end_date(year: int) -> str:
    """해당 연도의 마지막 KOSPI 거래일."""
    try:
        df = stock.get_market_ohlcv(f"{year}1220", f"{year}1231", "005930")
        if df is not None and not df.empty:
            return df.index[-1].strftime("%Y%m%d")
    except Exception:
        pass
    return f"{year}1228"  # fallback


def _pbr_snapshot(date_str: str) -> pd.DataFrame | None:
    """특정 날짜의 KOSPI 전체 종목 PBR.

    pykrx get_market_fundamental_by_ticker(date, market) 사용.
    """
    try:
        df = stock.get_market_fundamental_by_ticker(date_str, market="KOSPI")
    except Exception as e:
        print(f"    PBR 스냅샷 오류: {e}")
        return None
    if df is None or df.empty or "PBR" not in df.columns:
        return None
    df = df[["PBR"]].copy()
    df.index = df.index.astype(str).str.zfill(6)
    df = df[df["PBR"] > 0]
    return df


# ─────────────────────────────────────────────────────────────
# 수익률 계산
# ─────────────────────────────────────────────────────────────

def _stock_return(ticker: str, start_date: str, end_date: str) -> float | None:
    df = fetch_with_retry(
        stock.get_market_ohlcv,
        start_date, end_date, ticker,
        retries=2, sleep=0.3,
    )
    if df is None or df.empty or "종가" not in df.columns or len(df) < 2:
        return None
    p0, p1 = float(df["종가"].iloc[0]), float(df["종가"].iloc[-1])
    if p0 <= 0:
        return None
    return round((p1 / p0 - 1) * 100, 1)


# ─────────────────────────────────────────────────────────────
# 메인 로직
# ─────────────────────────────────────────────────────────────

def scan(since_year: int, max_pbr: float, rerate_pbr: float,
         horizon_years: int, tickers: list[str] | None,
         workers: int) -> list[dict]:
    """저PBR → 리레이팅 사례 스캔."""
    today = datetime.now()
    scan_years = list(range(since_year, today.year - 1))  # 최소 horizon 2년 확보
    if not scan_years:
        print("스캔 가능 연도 없음 (since_year가 너무 최근)")
        return []

    print(f"스캔 연도: {scan_years[0]} ~ {scan_years[-1]}, "
          f"조건: PBR ≤ {max_pbr} → {horizon_years}년 내 PBR ≥ {rerate_pbr}")

    # DART 공시 데이터 (activism 교차용)
    activism_tickers: set[str] = set()
    if Path(ENRICHED_JSON).exists():
        enriched = json.load(open(ENRICHED_JSON, encoding="utf-8"))
        for tk, d in enriched.items():
            for h in d.get("major_holdings_5pct", []):
                ft = h.get("filer_type", "")
                if ft in ("activist", "semi_activist", "pe_fund"):
                    activism_tickers.add(tk)
        print(f"activism 교차 대상 (enriched.json): {len(activism_tickers)}종목")

    results = []

    for base_year in scan_years:
        print(f"\n[{base_year}] 연말 PBR 스냅샷 로드 ...")
        # 연말 PBR
        end_of_year = f"{base_year}1228"
        try:
            snap_base = _pbr_snapshot(end_of_year)
        except Exception as e:
            print(f"  실패: {e}")
            continue
        if snap_base is None or snap_base.empty:
            print(f"  데이터 없음")
            continue

        # 저PBR 후보 필터
        low_pbr = snap_base[snap_base["PBR"] <= max_pbr].copy()
        if tickers:
            low_pbr = low_pbr[low_pbr.index.isin(tickers)]
        print(f"  PBR ≤ {max_pbr} 종목: {len(low_pbr)}개")
        if low_pbr.empty:
            continue

        # horizon 후 PBR (연말 기준)
        horizon_year = base_year + horizon_years
        if horizon_year > today.year:
            horizon_year = today.year
        horizon_date = _year_end_date(horizon_year)
        snap_end = _pbr_snapshot(horizon_date)

        # 각 종목 처리
        def process_one(tk: str, pbr_base: float) -> dict | None:
            pbr_end = None
            if snap_end is not None and tk in snap_end.index:
                pbr_end = float(snap_end.loc[tk, "PBR"])

            rerating = pbr_end is not None and pbr_end >= rerate_pbr
            ret = None
            if rerating:
                ret = _stock_return(tk, end_of_year,
                                    f"{horizon_year}0630")

            return {
                "ticker":       tk,
                "base_year":    base_year,
                "pbr_base":     round(pbr_base, 2),
                "pbr_after":    round(pbr_end, 2) if pbr_end else None,
                "rerating":     rerating,
                "ret_pct":      ret,
                "activism":     tk in activism_tickers,
            }

        batch = list(low_pbr.itertuples())
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_one, row.Index, row.PBR): row.Index
                    for row in batch}
            for fut in as_completed(futs):
                try:
                    r = fut.result()
                    if r:
                        results.append(r)
                except Exception:
                    pass
                done += 1
                if done % 50 == 0:
                    print(f"  ... {done}/{len(batch)}")

        time.sleep(0.5)

    return results


# ─────────────────────────────────────────────────────────────
# 요약 출력
# ─────────────────────────────────────────────────────────────

def _print_results(results: list[dict], max_pbr: float,
                   rerate_pbr: float, horizon_years: int) -> None:
    total = len(results)
    rerating = [r for r in results if r["rerating"]]
    activism_rerate = [r for r in rerating if r["activism"]]
    no_activism_rerate = [r for r in rerating if not r["activism"]]

    print(f"\n{'='*65}")
    print(f"  리레이팅 스캔 결과 (PBR≤{max_pbr} → {horizon_years}년 내 PBR≥{rerate_pbr})")
    print(f"{'='*65}")
    print(f"  전체 저PBR 이벤트 : {total}건")
    rr_rate = len(rerating) / total * 100 if total else 0
    print(f"  리레이팅 성공      : {len(rerating)}건 ({rr_rate:.1f}%)")
    print(f"    - 행동주의 동반  : {len(activism_rerate)}건")
    print(f"    - 행동주의 없음  : {len(no_activism_rerate)}건")

    def avg_ret(lst):
        v = [r["ret_pct"] for r in lst if r.get("ret_pct") is not None]
        return f"{sum(v)/len(v):.1f}%" if v else "N/A"

    if rerating:
        print(f"\n  리레이팅 성공 평균 수익률: {avg_ret(rerating)}")
        print(f"    행동주의 동반: {avg_ret(activism_rerate)}")
        print(f"    행동주의 없음: {avg_ret(no_activism_rerate)}")

    # 상위 케이스
    top = sorted(rerating, key=lambda r: r.get("ret_pct") or -999, reverse=True)[:20]
    if top:
        print(f"\n  리레이팅 상위 20개 케이스:")
        print(f"  {'티커':8} {'연도':6} {'기초PBR':>8} {'이후PBR':>8}"
              f" {'수익률':>8} {'행동주의':>6}")
        for r in top:
            act = "✓" if r["activism"] else ""
            ret = f"{r['ret_pct']:+.0f}%" if r.get("ret_pct") is not None else "—"
            print(f"  {r['ticker']:8} {r['base_year']:6} {r['pbr_base']:>8.2f}"
                  f" {r['pbr_after'] or 0:>8.2f} {ret:>8} {act:>6}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    require("KRX_ID", "KRX_PW")
    parser = argparse.ArgumentParser(description="저PBR → 리레이팅 과거 사례 스캔")
    parser.add_argument("--since-year",  type=int,   default=2018,
                        help="스캔 시작 연도 (기본 2018)")
    parser.add_argument("--max-pbr",     type=float, default=0.8,
                        help="기준 저PBR 임계값 (기본 0.8)")
    parser.add_argument("--rerate-pbr",  type=float, default=1.0,
                        help="리레이팅 인정 PBR (기본 1.0)")
    parser.add_argument("--horizon",     type=int,   default=3,
                        help="리레이팅 판단 기간(년, 기본 3)")
    parser.add_argument("--tickers",     default="",
                        help="콤마 구분 티커 (비어있으면 KOSPI 전체)")
    parser.add_argument("--workers",     type=int,   default=1,
                        help="병렬 워커 수 (KRX 세션 충돌 주의, 기본 1)")
    parser.add_argument("--out",         default=str(RERATE_JSON))
    args = parser.parse_args()

    tickers = [t.strip().zfill(6) for t in args.tickers.split(",") if t.strip()] or None

    results = scan(
        since_year=args.since_year,
        max_pbr=args.max_pbr,
        rerate_pbr=args.rerate_pbr,
        horizon_years=args.horizon,
        tickers=tickers,
        workers=args.workers,
    )

    if not results:
        print("결과 없음")
        return

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "params": vars(args),
            "total": len(results),
            "rerating_count": sum(1 for r in results if r["rerating"]),
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {args.out} ({len(results)}건)")

    _print_results(results, args.max_pbr, args.rerate_pbr, args.horizon)


if __name__ == "__main__":
    main()
