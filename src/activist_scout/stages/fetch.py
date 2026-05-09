"""
KRX 데이터 수집 스테이지 (flow + liquidity 통합).

사용법:
  python fetch.py --mode flow       기관 순매수 / 매수 VWAP
  python fetch.py --mode liquidity  ADV 20D / 5% 매집 소요일
  python fetch.py                   flow → liquidity 순서로 모두 실행

참고: KRX 세션이 멀티스레드에서 충돌 → --workers 기본값 1 권장.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

from activist_scout.config import FLOW_CSV, LIQUIDITY_CSV, SCREENING_CSV, require
from activist_scout.utils import fetch_with_retry


# ─────────────────────────────────────────────────────────────
# 기관 플로우 (flow)
# ─────────────────────────────────────────────────────────────

def _date_range(days_back: int) -> tuple[str, str]:
    end = datetime.now()
    return (end - timedelta(days=days_back)).strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _latest_close(ticker: str) -> float | None:
    end = datetime.now()
    start = end - timedelta(days=10)
    df = fetch_with_retry(
        stock.get_market_ohlcv,
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker,
    )
    if df is None or df.empty:
        return None
    return float(df["종가"].iloc[-1])


def _inst_metrics(ticker: str, fromdate: str, todate: str) -> dict | None:
    """기관 net 매수량/금액 + 매수 VWAP (net-buy 일자만)."""
    vol = fetch_with_retry(stock.get_market_trading_volume_by_date, fromdate, todate, ticker)
    val = fetch_with_retry(stock.get_market_trading_value_by_date, fromdate, todate, ticker)
    if vol is None or val is None:
        return None
    if "기관합계" not in vol.columns or "기관합계" not in val.columns:
        return None

    v = vol["기관합계"].astype(float)
    a = val["기관합계"].astype(float)
    buy_mask = v > 0
    buy_vol = float(v[buy_mask].sum())
    buy_val = float(a[buy_mask].sum())
    buy_vwap = buy_val / buy_vol if buy_vol > 0 else None

    return {
        "net_value_원": float(a.sum()),
        "days_total": len(vol),
        "days_buy": int((v > 0).sum()),
        "buy_vwap_원": buy_vwap,
    }


def _flow_one(ticker: str, name: str) -> dict | None:
    today = datetime.now().strftime("%Y%m%d")
    f90, _ = _date_range(130)
    f20, _ = _date_range(30)
    m90 = _inst_metrics(ticker, f90, today)
    m20 = _inst_metrics(ticker, f20, today)
    px = _latest_close(ticker)
    if not m90 or not m20 or px is None:
        return None

    def vs(vwap):
        return None if not vwap else round((px / vwap - 1) * 100, 1)

    return {
        "ticker": ticker,
        "name": name,
        "현재가": int(px),
        "기관순매수_90D(억)": round(m90["net_value_원"] / 1e8, 1),
        "순매수일/총_90D": f"{m90['days_buy']}/{m90['days_total']}",
        "기관매수VWAP_90D": int(m90["buy_vwap_원"]) if m90["buy_vwap_원"] else None,
        "vs_매수VWAP_90D(%)": vs(m90["buy_vwap_원"]),
        "기관순매수_20D(억)": round(m20["net_value_원"] / 1e8, 1),
        "순매수일/총_20D": f"{m20['days_buy']}/{m20['days_total']}",
        "기관매수VWAP_20D": int(m20["buy_vwap_원"]) if m20["buy_vwap_원"] else None,
        "vs_매수VWAP_20D(%)": vs(m20["buy_vwap_원"]),
    }


def run_flow(input_csv: str, out_csv: str, workers: int) -> None:
    df = pd.read_csv(input_csv, dtype={"ticker": str})
    print(f"[flow] 입력 {len(df)}개 → 기관 순매수 분석 (workers={workers})")
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_flow_one, r["ticker"], r["name"]): r["ticker"]
                for _, r in df.iterrows()}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
                if res:
                    rows.append(res)
            except Exception:
                pass
            if i % 10 == 0:
                print(f"  ... {i}/{len(futs)}")
    if not rows:
        print("[flow] 결과 없음")
        return
    out = pd.DataFrame(rows).sort_values("기관순매수_90D(억)", ascending=False)
    out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"[flow] 저장: {out_csv}")


# ─────────────────────────────────────────────────────────────
# 유동성 (liquidity)
# ─────────────────────────────────────────────────────────────

def _adv_20d(ticker: str) -> float | None:
    """최근 20 거래일 평균 일일 거래대금 (원). 종가×거래량 근사."""
    end = datetime.now()
    start = end - timedelta(days=40)
    df = fetch_with_retry(
        stock.get_market_ohlcv,
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker,
    )
    if df is None or df.empty:
        return None
    if "종가" not in df.columns or "거래량" not in df.columns:
        return None
    last20 = df.tail(20)
    if last20.empty:
        return None
    return float((last20["종가"].astype(float) * last20["거래량"].astype(float)).mean())


def _liq_one(ticker: str, name: str, mcap_won: float) -> dict | None:
    adv = _adv_20d(ticker)
    if adv is None or adv == 0:
        return None
    target_5pct = mcap_won * 0.05
    days_5 = target_5pct / adv
    if days_5 <= 30:
        capacity = 1.0
    elif days_5 >= 90:
        capacity = 0.0
    else:
        capacity = 1.0 - (days_5 - 30) / 60
    return {
        "ticker": ticker,
        "name": name,
        "시가총액(억)": round(mcap_won / 1e8, 0),
        "ADV_20D(억)": round(adv / 1e8, 2),
        "5%지분(억)": round(target_5pct / 1e8, 1),
        "days_to_5pct": round(days_5, 1),
        "capacity_score": round(capacity, 2),
    }


def run_liquidity(input_csv: str, out_csv: str, workers: int) -> None:
    df = pd.read_csv(input_csv, dtype={"ticker": str})
    df["시가총액_원"] = df["시가총액(억)"].astype(float) * 1e8
    print(f"[liquidity] 입력 {len(df)}개 → 유동성 분석 (workers={workers})")
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(_liq_one, r["ticker"], r["name"], r["시가총액_원"]): r["ticker"]
            for _, r in df.iterrows()
        }
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
                if res:
                    rows.append(res)
            except Exception:
                pass
            if i % 10 == 0:
                print(f"  ... {i}/{len(futs)}")
    if not rows:
        print("[liquidity] 결과 없음")
        return
    out = pd.DataFrame(rows).sort_values("days_to_5pct")
    out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"[liquidity] 저장: {out_csv}")
    print(f"  - capacity≥0.5 (≤60일): {(out['capacity_score'] >= 0.5).sum()}개")
    print(f"  - capacity≥0.8 (≤42일): {(out['capacity_score'] >= 0.8).sum()}개")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    require("KRX_ID", "KRX_PW")
    parser = argparse.ArgumentParser(description="KRX fetch: flow / liquidity")
    parser.add_argument("--mode", choices=["flow", "liquidity", "all"], default="all",
                        help="실행 모드 (기본: all — flow 후 liquidity)")
    parser.add_argument("--input", default=str(SCREENING_CSV))
    parser.add_argument("--flow-out", default=str(FLOW_CSV))
    parser.add_argument("--liq-out", default=str(LIQUIDITY_CSV))
    parser.add_argument("--workers", type=int, default=1,
                        help="KRX 세션 충돌 방지를 위해 1 권장")
    args = parser.parse_args()

    if args.mode in ("flow", "all"):
        run_flow(args.input, args.flow_out, args.workers)
    if args.mode in ("liquidity", "all"):
        run_liquidity(args.input, args.liq_out, args.workers)


if __name__ == "__main__":
    main()
