"""Central configuration: credentials, paths, scoring constants.

Environment variable load priority:
  1) Process environment
  2) `<repo>/.env`
  3) `~/.activist-scout/.env`  (optional, for shared multi-repo setups)

Modify constants here once — pipeline-wide effect.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of `src/`. So src/activist_scout/config.py → ../../
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent

# Load .env from project root (preferred) and home as fallback
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(Path.home() / ".activist-scout" / ".env", override=False)


def _env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    return v if v else default


# ─────────────────────────────────────────────────────────────────────────────
# API credentials
# ─────────────────────────────────────────────────────────────────────────────
KRX_ID         = _env("KRX_ID")
KRX_PW         = _env("KRX_PW")
DART_API_KEY   = _env("DART_API_KEY")
GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL   = _env("GEMINI_MODEL", "gemini-2.5-pro")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL   = _env("OPENAI_MODEL", "gpt-5.4-mini")

# pykrx reads creds from environment vars only
if KRX_ID:
    os.environ["KRX_ID"] = KRX_ID
if KRX_PW:
    os.environ["KRX_PW"] = KRX_PW


# ─────────────────────────────────────────────────────────────────────────────
# Output paths — all artifacts under <project>/data/ (gitignored)
# ─────────────────────────────────────────────────────────────────────────────
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

REPORTS_DIR = PROJECT_ROOT / "reports"

# Shared
CORP_MAP_FILE          = DATA_DIR / "corp_code_map.json"

# Backtest (slow, quarterly — calibration source)
BACKTEST_ACTIVIST_JSON = DATA_DIR / "backtest_activist.json"
BACKTEST_ACTIVIST_MD   = DATA_DIR / "backtest_activist.md"

# Screening pipeline (weekly/monthly)
SCREENING_CSV          = DATA_DIR / "screening_value_ownership.csv"
FLOW_CSV               = DATA_DIR / "institutional_flow.csv"
LIQUIDITY_CSV          = DATA_DIR / "liquidity.csv"
ENRICHED_JSON          = DATA_DIR / "enriched.json"
CLASSIFICATION_JSON    = DATA_DIR / "classification.json"
CLASSIFICATION_CSV     = DATA_DIR / "classification.csv"
SECTOR_PBR_JSON        = DATA_DIR / "sector_pbr.json"
SCORES_JSON            = DATA_DIR / "scores.json"
SCORES_CSV             = DATA_DIR / "scores.csv"
REPORT_MD              = DATA_DIR / "report.md"


# ─────────────────────────────────────────────────────────────────────────────
# v5 scoring constants (3-axis rule-based)
#
# Why rule-based: regime change (상법 amendment) makes historical Beta-binomial
# calibration unreliable. First-principles weights here, all reviewable.
# ─────────────────────────────────────────────────────────────────────────────

# Axis 1 — TARGET ATTRACTIVENESS (does an activist fund find this attractive?)
TARGET_WEIGHTS = {
    "deep_cash":           30,   # excess capital ratio ≥ 0.50
    "moderate_cash":       20,   # 0.20 ~ 0.50 (exclusive with deep_cash)
    "owner_sweet_spot":    20,   # owner stake 40 ~ 55%
    "pbr_gap_30pct":       15,   # PBR ≤ sector median × 0.7
    "cap_large":           10,   # market cap ≥ 200B KRW
    "cap_mid":              5,   # market cap 50B ~ 200B (exclusive with cap_large)
    "self_treasury_5pct":  15,   # self-held treasury stock ≥ 5%
    "nav_discount_50":     35,   # NAV discount ≥ 50% (deep holdco value)
    "nav_discount_30":     20,   # NAV discount 30 ~ 50% (exclusive with nav_50)
}

# Axis 2 — ACCUMULATION SIGNATURE (already being quietly accumulated?)
ACCUM_WEIGHTS = {
    "buy_days_65pct":      25,   # ≥ 65% of 90 trading days net-bought
    "net_buy_3pct":        25,   # 90D cumulative net buy ≥ 3% of mcap
    "vwap_near":           20,   # |current price - buy VWAP| ≤ 5%
    "buy_days_50_short":   15,   # 20D buying continuity ≥ 50%
    "high_capacity":       15,   # liquidity capacity ≥ 0.7
    "stake_secrecy":       10,   # daily buy / ADV ≤ 5% (covert accumulation)
}

# Axis 3 — LEGAL VULNERABILITY (post-amendment 상법 leverage)
LEGAL_WEIGHTS = {
    "treasury_dispose":         20,   # treasury share disposal in last 12M
    "trust_cancel":             15,   # buyback trust cancellation in last 12M
    "split_filing":             15,   # split/merger filing in last 24M
    "exec_churn":               10,   # abnormal executive turnover
    "audit_or_halt":            20,   # trading halt / audit qualification in last 5Y
    "related_party":            20,   # related-party transaction abuse (LLM-quantified)
    "outside_director_expiry": 15,    # outside director term ending ≤ 180D
}

# Catalyst timing — outside director term expiry window
CATALYST_TIMING_DAYS = 180

# Tier classification thresholds
TIER_THRESHOLDS = {
    "axis_strong":   60,         # an axis ≥ 60 = "strong"
    "axis_v_strong": 70,         # more strict (single-axis WATCH qualification)
}

# LATE sub-classification (post-filing market reaction)
LATE_FILING_DAYS = 365
LATE_THRESHOLDS = {
    "priced_in_alpha":  20.0,    # ≥ +20% → LATE_PRICED_IN (already reflected)
    "skeptical_alpha":  -5.0,    # < -5%  → LATE_SKEPTICAL (paradoxical opportunity)
    # in between        → LATE_ACCESSIBLE
}

# AVOID rules — explicitly excluded from screening
# 60→65 relaxed: post-amendment 상법 makes 60%+ partially attackable
AVOID_OWNER_PCT = 65.0

# AVOID exemption rules (only applies in 60~65% band)
AVOID_EXEMPT_NAV_DISCOUNT   = -30.0   # NAV discount ≤ -30% → exempt (deep holdco value)
AVOID_EXEMPT_TREASURY_SCORE = 1.0     # treasury_score ≥ +1.0 → exempt (buyback intent)

# Captive subsidiary detection (forced AVOID)
CAPTIVE_RELATED_PARTY_PCT = 50.0      # related-party sales ≥ 50% AND ...
CAPTIVE_PARENT_STAKE_PCT  = 50.0      # ... strategic parent stake ≥ 50%


# ─────────────────────────────────────────────────────────────────────────────
# Domain constants
# ─────────────────────────────────────────────────────────────────────────────
HOLDINGS_CUTOFF_DATE = "20250101"     # show 5%+ filings filed after this date

DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"


def require(*keys: str) -> None:
    """Raise immediately if required env vars are missing."""
    missing = [k for k in keys if not _env(k)]
    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}. "
            f"See .env.example."
        )
