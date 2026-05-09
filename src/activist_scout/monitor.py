"""
일별 DART filing 모니터링 — watchlist 종목에 행동주의 5%+ filing 발생 시 알림.

사용법:
  python monitor.py                       # 어제 ~ 오늘 filing 스캔, watchlist 매칭
  python monitor.py --days 7              # 최근 7일
  python monitor.py --since 20260501      # 특정일 이후

watchlist 정의:
  scores.json 의 모든 종목 (HOT/WARM/WATCH/LATE_*/PASS 포함, AVOID 제외)
  → 이미 진입한 종목의 *추가* filing 알림 + 현재 PASS 종목의 *신규 진입* 알림 모두 capture

cron 예시 (매일 오전 9시):
  0 9 * * * cd /path/to/screening && python monitor.py 2>&1 >> monitor.log
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from activist_scout.config import DART_VIEWER_URL, SCORES_JSON, require
from activist_scout.domain import classify_filer
from activist_scout.utils import dart_get


def load_watchlist() -> dict:
    """scores.json 의 ticker → row 매핑. AVOID 만 제외."""
    if not Path(SCORES_JSON).exists():
        raise SystemExit(f"{SCORES_JSON.name} 없음. 먼저 score_targets.py 실행하세요.")
    data = json.load(open(SCORES_JSON, encoding="utf-8"))
    return {r["ticker"]: r for r in data["rows"] if r["tier"] != "AVOID"}


def fetch_filings(bgn: str, end: str) -> list[dict]:
    """DART list.json 으로 해당 기간 모든 5%+ 보고 (pblntf_ty=D) 수집."""
    out = []
    page = 1
    while page <= 20:
        j = dart_get(
            "list.json",
            {"bgn_de": bgn, "end_de": end, "pblntf_ty": "D",
             "page_no": page, "page_count": 100},
        )
        if not j or j.get("status") != "000":
            break
        out.extend(j.get("list", []))
        if int(j.get("page_no", 1)) >= int(j.get("total_page", 1)):
            break
        page += 1
    return out


def main():
    require("DART_API_KEY")
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="최근 N일 (default 1)")
    parser.add_argument("--since", default=None, help="YYYYMMDD 이후")
    args = parser.parse_args()

    end = datetime.now().strftime("%Y%m%d")
    bgn = args.since or (datetime.now() - timedelta(days=args.days)).strftime("%Y%m%d")

    watchlist = load_watchlist()
    print(f"watchlist: {len(watchlist)}개 종목 (AVOID 제외)")

    filings = fetch_filings(bgn, end)
    print(f"DART {bgn} ~ {end}: 5%+ 보고 {len(filings)}건")

    hits = []
    for f in filings:
        sc = f.get("stock_code", "") or ""
        if sc not in watchlist:
            continue
        flr = f.get("flr_nm", "") or ""
        ftype = classify_filer(flr)
        if ftype not in ("activist", "semi_activist", "pe_fund"):
            continue
        hits.append({**f, "filer_type": ftype, "watch_row": watchlist[sc]})

    if not hits:
        print(f"\n📭 watchlist 종목에 행동주의/PE 신규 filing 없음 ({bgn}~{end})")
        return

    print(f"\n🚨 {len(hits)}건 발견:\n")
    print(f"{'rcept_dt':<12}{'tier':<18}{'ticker':<8}{'name':<14}"
          f"{'type':<14}{'filer':<30}{'url'}")
    print("-" * 130)
    for h in sorted(hits, key=lambda x: x.get("rcept_dt", "")):
        url = DART_VIEWER_URL.format(h.get("rcept_no", ""))
        wr = h.get("watch_row") or {}
        print(
            f"{h.get('rcept_dt','?'):<12}"
            f"{wr.get('tier','?'):<18}"
            f"{h.get('stock_code','?'):<8}"
            f"{(h.get('corp_name') or '')[:13]:<14}"
            f"{h.get('filer_type','?'):<14}"
            f"{(h.get('flr_nm') or '')[:28]:<30}"
            f"{url}"
        )
    print()


if __name__ == "__main__":
    main()
