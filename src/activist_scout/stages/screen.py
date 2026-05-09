"""
KOSPI 멀티조건 1차 스크리너.

조건:
  1) PBR ≤ pbr_max  (pykrx)
  2) 최근 N분기 영업이익 > 0  (yfinance Operating Income)
  3) 최근 4년 매출 단조증가 = 3Y YoY+  (yfinance annual Revenue)
  4) 최대주주+특수관계인 보통주 합산 지분율 ≥ owner_min%  (DART hyslrSttus)
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import yfinance as yf
from pykrx import stock

from activist_scout.config import SCREENING_CSV, require
from activist_scout.utils import dart_get, latest_business_day, load_corp_map


# ---- 1차 필터: PBR universe ----
def fetch_pbr_universe(date: str, pbr_max: float) -> pd.DataFrame:
    fund = stock.get_market_fundamental(date, market="KOSPI")
    cap = stock.get_market_cap(date, market="KOSPI")[["시가총액"]]
    df = fund.join(cap, how="left")
    df = df[(df["PBR"] > 0) & (df["PBR"] <= pbr_max)].copy()
    df["name"] = [stock.get_market_ticker_name(t) for t in df.index]
    df = df.reset_index().rename(columns={"티커": "ticker"})
    df["yf_symbol"] = df["ticker"].astype(str) + ".KS"
    return df.sort_values("PBR")


# ---- 2차 필터: 분기 영업이익 ----
def operating_profit_ok(yf_symbol: str, n: int = 3) -> tuple[bool, list[float] | None]:
    try:
        qf = yf.Ticker(yf_symbol).quarterly_financials
        if qf is None or qf.empty or "Operating Income" not in qf.index:
            return False, None
        ser = qf.loc["Operating Income"].dropna()
        if len(ser) < n:
            return False, None
        cols = sorted(ser.index, reverse=True)[:n]
        vals = [float(ser[c]) for c in cols]
        return all(v > 0 for v in vals), vals
    except Exception:
        return False, None


# ---- 3차 필터: 연 매출 YoY 증가 ----
def revenue_growing(yf_symbol: str, years: int = 4) -> tuple[bool, list[float] | None]:
    try:
        fin = yf.Ticker(yf_symbol).financials
        if fin is None or fin.empty or "Total Revenue" not in fin.index:
            return False, None
        ser = fin.loc["Total Revenue"].dropna()
        if len(ser) < years:
            return False, None
        cols = sorted(ser.index, reverse=True)[:years]
        vals = [float(ser[c]) for c in cols]
        ok = all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
        return ok, vals
    except Exception:
        return False, None


# ---- 잉여자본 (행동주의 핵심 시그널) ----
def excess_capital_ratio(yf_symbol: str, market_cap_won: float) -> dict | None:
    """순현금 / 시가총액. 행동주의는 잉여자본이 큰 회사를 노림.

    net_cash = cash + ST investments - total debt
    > 0.30 이면 강한 후보, > 0.50 이면 deep cash treasure.
    """
    if market_cap_won <= 0:
        return None
    try:
        bs = yf.Ticker(yf_symbol).balance_sheet
        if bs is None or bs.empty:
            return None
        # 가장 최근 분기/연
        col = sorted(bs.columns, reverse=True)[0]

        def get(*keys):
            for k in keys:
                if k in bs.index:
                    v = bs.loc[k, col]
                    if v is not None and not pd.isna(v):
                        return float(v)
            return 0.0

        cash = get("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
        st_inv = get("Other Short Term Investments", "Short Term Investments")
        total_debt = get("Total Debt", "Long Term Debt")
        if total_debt == 0:
            total_debt = get("Long Term Debt") + get("Current Debt", "Short Term Debt")

        net_cash = cash + st_inv - total_debt
        ratio = net_cash / market_cap_won
        return {
            "cash_원": cash,
            "st_inv_원": st_inv,
            "total_debt_원": total_debt,
            "net_cash_원": net_cash,
            "excess_capital_ratio": round(ratio, 3),
            "as_of": str(col)[:10],
        }
    except Exception:
        return None


# ---- 4차 필터: 최대주주+특수관계인 지분율 ----
def major_holder_pct(corp_code: str, year: int) -> float | None:
    """보통주 '계'(소계) 행 → 최대주주+특수관계인 합산. 미공시면 분기/반기로 fallback."""
    for reprt in ("11011", "11014", "11013", "11012"):
        j = dart_get(
            "hyslrSttus.json",
            {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt},
        )
        if not j or j.get("status") != "000":
            continue

        # 1순위: 보통주 "계" 행
        for row in j.get("list", []):
            if row.get("stock_knd") != "보통주":
                continue
            if (row.get("nm") or "").strip() != "계":
                continue
            for fld in ("trmend_posesn_stock_qota_rt", "bsis_posesn_stock_qota_rt"):
                s = (row.get(fld) or "").replace(",", "").strip()
                if s and s != "-":
                    try:
                        return round(float(s), 2)
                    except ValueError:
                        pass

        # 2순위: 보통주 개별 행 합산
        total = 0.0
        for row in j.get("list", []):
            if row.get("stock_knd") != "보통주":
                continue
            if (row.get("nm") or "").strip() == "계":
                continue
            s = ((row.get("trmend_posesn_stock_qota_rt") or row.get("bsis_posesn_stock_qota_rt") or "")
                 .replace(",", "").strip())
            if s and s != "-":
                try:
                    total += float(s)
                except ValueError:
                    pass
        if total > 0:
            return round(total, 2)
    return None


def screen_one(row: pd.Series, corp_map: dict, year: int, owner_min: float,
               min_q: int, min_excess_cap: float | None):
    cm = corp_map.get(row["ticker"])
    if not cm:
        return None
    pct = major_holder_pct(cm["corp_code"], year)
    if pct is None or pct < owner_min:
        return None

    op_ok, op_vals = operating_profit_ok(row["yf_symbol"], n=min_q)
    if not op_ok:
        return None

    rev_ok, rev_vals = revenue_growing(row["yf_symbol"])
    if not rev_ok:
        return None

    cap = excess_capital_ratio(row["yf_symbol"], float(row["시가총액"]))
    if min_excess_cap is not None and (cap is None or cap["excess_capital_ratio"] < min_excess_cap):
        return None

    return {
        "ticker": row["ticker"],
        "name": row["name"],
        "PBR": row["PBR"],
        "PER": row["PER"],
        "시가총액(억)": round(row["시가총액"] / 1e8, 0),
        "최대주주_지분율(%)": pct,
        "잉여자본비율": cap["excess_capital_ratio"] if cap else None,
        "순현금(억)": round(cap["net_cash_원"] / 1e8, 0) if cap else None,
        "영업이익_3Q(백만원)": [round(v / 1e6, 0) for v in op_vals],
        "매출_4Y(억원)": [round(v / 1e8, 0) for v in rev_vals],
    }


def main():
    require("KRX_ID", "KRX_PW", "DART_API_KEY")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbr-max", type=float, default=0.8)
    parser.add_argument("--owner-min", type=float, default=40.0)
    parser.add_argument("--min-quarters", type=int, default=3,
                        help="연속 영업이익 흑자 분기 수")
    parser.add_argument("--min-excess-cap", type=float, default=None,
                        help="순현금/시총 최소값(예 0.20). 미지정 시 정보용 컬럼만 추가, 필터 미적용")
    parser.add_argument("--year", type=int, default=datetime.now().year - 1)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    corp_map = load_corp_map()
    base_date = args.date or latest_business_day()
    print(f"[1/3] 기준일 {base_date}, DART 사업보고서 {args.year}")

    print(f"[2/3] KOSPI · PBR ≤ {args.pbr_max} 종목 추출")
    universe = fetch_pbr_universe(base_date, args.pbr_max)
    print(f"      → {len(universe)}개")
    if args.limit:
        universe = universe.head(args.limit)
        print(f"      → --limit 적용: {len(universe)}개")

    print(f"[3/3] 멀티조건 검증 (workers={args.workers})")
    matches: list[dict] = []
    failed = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(screen_one, row, corp_map, args.year, args.owner_min,
                      args.min_quarters, args.min_excess_cap): row["ticker"]
            for _, row in universe.iterrows()
        }
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
                if res:
                    matches.append(res)
            except Exception:
                failed += 1
            if i % 25 == 0:
                el = time.time() - t0
                rate = i / el if el else 0
                rem = (len(futs) - i) / rate if rate else 0
                print(f"      ... {i}/{len(futs)}  매치={len(matches)}  실패={failed}  잔여≈{rem:.0f}s")

    print(f"\n=== 결과: {len(matches)}개 종목 ===")
    if not matches:
        return
    out = pd.DataFrame(matches).sort_values("PBR")
    out.to_csv(SCREENING_CSV, index=False, encoding="utf-8-sig")
    print(f"저장: {SCREENING_CSV}")


if __name__ == "__main__":
    main()
