"""
P4 v7 — 신호 시간 분리 분석 (causal validation v1).

목적: 상법 개정 (2024 이후) regime change가 행동주의 5%+ filing 의 forward alpha 에
실제 영향을 주었는지 분리 측정.

Pre-amendment: filing_date < 2024-07-01
Post-amendment: filing_date ≥ 2024-07-01

backtest_activist.json 의 events 를 시간 분리 후 alpha 통계 비교.

한계:
- post-amendment 표본 크기 작음 (현재 시점 기준 1.5~2년)
- 12M alpha 측정 가능한 표본만 (filing 후 12M 경과 필요)
- 24M alpha 는 사실상 측정 불가

사용법:
  python validate_signals.py
"""
from __future__ import annotations

import json
import statistics

from activist_scout.config import BACKTEST_ACTIVIST_JSON


REGIME_BREAK = "2024-07-01"   # 이사 충실의무 주주 포함 개정 검토 본격화 시점


def main():
    bt = json.load(open(BACKTEST_ACTIVIST_JSON, encoding="utf-8"))
    events = bt.get("events", [])
    if not events:
        raise SystemExit("backtest_activist.json events 없음")

    pre = [e for e in events if e["filing_date"] < REGIME_BREAK]
    post = [e for e in events if e["filing_date"] >= REGIME_BREAK]
    print(f"전체 {len(events)}건 — pre {len(pre)} / post {len(post)} (break {REGIME_BREAK})")

    def stats(values, label):
        v = [x for x in values if x is not None]
        if not v:
            return f"{label}: n=0"
        return (f"{label}: n={len(v):>4} mean={statistics.mean(v):>+6.1f}% "
                f"median={statistics.median(v):>+6.1f}% "
                f"win={sum(1 for x in v if x>0)/len(v)*100:>5.1f}%")

    print("\n=== filer_type × regime ===")
    for ftype in ("activist", "semi_activist"):
        for periods, label in [(pre, "pre "), (post, "post")]:
            sub = [e for e in periods if e["filer_type"] == ftype]
            print(f"  {ftype:<14} {label}: "
                  + stats((e.get("alpha_12M") for e in sub), "α 12M")
                  + " | "
                  + stats((e.get("alpha_24M") for e in sub), "α 24M"))

    print("\n=== PBR bucket × regime (alpha_12M) ===")
    def pbr_bucket(p):
        if p is None: return "?"
        if p < 0.4: return "lo"
        if p < 0.6: return "mid"
        return "hi"
    for bucket in ("lo", "mid", "hi"):
        for periods, label in [(pre, "pre "), (post, "post")]:
            sub = [e for e in periods if pbr_bucket(e.get("pbr_at_filing")) == bucket]
            print(f"  PBR {bucket:<3} {label}: "
                  + stats((e.get("alpha_12M") for e in sub), "α 12M"))

    print("\n=== 결론 ===")
    pre_a12 = [e["alpha_12M"] for e in pre if e.get("alpha_12M") is not None]
    post_a12 = [e["alpha_12M"] for e in post if e.get("alpha_12M") is not None]
    if pre_a12 and post_a12:
        pre_mean = statistics.mean(pre_a12)
        post_mean = statistics.mean(post_a12)
        delta = post_mean - pre_mean
        print(f"  pre  α12M 평균: {pre_mean:+.2f}% (n={len(pre_a12)})")
        print(f"  post α12M 평균: {post_mean:+.2f}% (n={len(post_a12)})")
        print(f"  Δ (post − pre): {delta:+.2f}%")
        if len(post_a12) < 10:
            print(f"  ⚠️  post 표본 {len(post_a12)} < 10 — 통계적 결론 미보장")
        else:
            print(f"  → {'regime change effect 양수' if delta > 0 else '효과 미확인'}")
    else:
        print("  데이터 부족")


if __name__ == "__main__":
    main()
