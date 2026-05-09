"""Pipeline orchestrator — three operational pipelines.

Conceptually the system has three pipelines:

1) BACKTEST (run quarterly — generates calibration prior)
   - DART 5%+ filings 10y → KOSPI alpha → backtest_activist.json
   - Used as historical reference (NOT calibration source in v5+).

2) SCREENING (run weekly/monthly — today's universe)
   - PBR/OI/revenue/owner filter → flow → liquidity → DART enrichment
   - LLM qualitative classification → 3-axis rule-based score → final report

3) DAILY (run daily as cron — alerts only)
   - monitor.py: new 5%+ filing → watchlist match → alert

corp_code mapping is shared (refresh quarterly).

Examples::

    python -m activist_scout.pipeline                          # screening (default)
    python -m activist_scout.pipeline --pipeline backtest      # quarterly backtest
    python -m activist_scout.pipeline --pipeline all           # full rebuild
    python -m activist_scout.pipeline --only score             # single stage
    python -m activist_scout.pipeline --from enrich            # from this stage
    python -m activist_scout.pipeline --skip fetch_flow        # skip specific stage(s)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

# Stage definitions — (name, module-path, [extra args])
SHARED = [
    ("corp_code", "activist_scout.utils", ["--build-corp-map"]),
]

BACKTEST_STAGES = [
    ("backtest", "activist_scout.stages.backtest", ["--since-year", "2015", "--max-pbr", "0.8"]),
]

SCREENING_STAGES = [
    ("screen",     "activist_scout.stages.screen",        []),
    ("fetch_flow", "activist_scout.stages.fetch",         ["--mode", "flow"]),
    ("fetch_liq",  "activist_scout.stages.fetch",         ["--mode", "liquidity"]),
    ("enrich",     "activist_scout.stages.enrich",        ["--report-text"]),
    ("parse_rp",   "activist_scout.stages.related_party", []),
    ("classify",   "activist_scout.stages.classify",      []),
    ("score",      "activist_scout.stages.score",         []),
    ("report",     "activist_scout.stages.report",        []),
]

DAILY_STAGES = [
    ("monitor", "activist_scout.monitor", []),
]


def all_stages(pipeline: str) -> list[tuple[str, str, list[str]]]:
    if pipeline == "backtest":
        return SHARED + BACKTEST_STAGES
    if pipeline == "screen":
        return SHARED + SCREENING_STAGES
    if pipeline == "daily":
        return DAILY_STAGES
    if pipeline == "all":
        return SHARED + BACKTEST_STAGES + SCREENING_STAGES
    raise ValueError(f"unknown pipeline: {pipeline}")


def run(name: str, module: str, extra: list[str]) -> None:
    cmd = [sys.executable, "-m", module] + extra
    cmd_str = " ".join(cmd)
    print(f"\n{'='*60}\n▶ [{name}] {cmd_str}\n{'='*60}")
    t0 = time.time()
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        sys.exit(f"❌ [{name}] failed (exit={proc.returncode})")
    print(f"✅ [{name}] ({time.time() - t0:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--pipeline", choices=["screen", "backtest", "daily", "all"],
        default="screen",
        help="Which pipeline to run (default: screen)",
    )
    parser.add_argument("--from", dest="start", default=None, help="Start from this stage")
    parser.add_argument("--only", default=None, help="Run only this stage")
    parser.add_argument("--skip", default="", help="Comma-separated stages to skip")
    args = parser.parse_args()

    stages = all_stages(args.pipeline)
    valid_names = {n for n, _, _ in stages}
    if args.start and args.start not in valid_names:
        sys.exit(f"--from {args.start}: not in '{args.pipeline}' pipeline. Available: {sorted(valid_names)}")
    if args.only and args.only not in valid_names:
        sys.exit(f"--only {args.only}: not in '{args.pipeline}' pipeline. Available: {sorted(valid_names)}")

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    started = args.start is None
    print(f"Pipeline: {args.pipeline} ({len(stages)} stages)")

    for name, module, extra in stages:
        if args.only:
            if name != args.only:
                continue
        else:
            if not started:
                if name == args.start:
                    started = True
                else:
                    continue
            if name in skip:
                print(f"⏭  [{name}] skipped")
                continue
        run(name, module, extra)


if __name__ == "__main__":
    main()
