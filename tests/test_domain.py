"""Smoke tests for domain.py — pure functions, no external deps."""
from __future__ import annotations

from activist_scout.domain import (
    agm_context,
    classify_filer,
    classify_governance_disclosure,
    classify_treasury,
    industry_peers,
    treasury_score,
)


class TestClassifyFiler:
    def test_activist_funds_detected(self):
        assert classify_filer("브이아이피자산운용") == "activist"
        assert classify_filer("얼라인파트너스자산운용") == "activist"
        assert classify_filer("KCGI자산운용") == "activist"

    def test_semi_activist(self):
        assert classify_filer("베어링자산운용") == "semi_activist"
        assert classify_filer("한국투자밸류자산운용") == "semi_activist"

    def test_passive_domestic(self):
        assert classify_filer("KB자산운용") == "passive"
        assert classify_filer("국민연금공단") == "passive"

    def test_passive_foreign(self):
        assert classify_filer("BlackRock Inc.") == "passive"
        assert classify_filer("Vanguard Group") == "passive"

    def test_pe_fund_keyword(self):
        assert classify_filer("프리미어성장전략엠앤에이사모투자합자회사") == "pe_fund"
        assert classify_filer("XYZ투자조합") == "pe_fund"

    def test_strategic(self):
        assert classify_filer("(주)모트렉스이에프엠") == "strategic"

    def test_individual(self):
        assert classify_filer("김종석") == "individual"
        assert classify_filer("홍길동") == "individual"

    def test_unknown(self):
        assert classify_filer("") == "unknown"


class TestClassifyTreasury:
    def test_burn(self):
        assert classify_treasury("자기주식 소각 결정") == "burn"

    def test_trust_cancel(self):
        assert classify_treasury("주요사항보고서(자기주식취득신탁계약해지결정)") == "trust_cancel"

    def test_trust_open(self):
        assert classify_treasury("자기주식취득 신탁계약 체결") == "trust_open"

    def test_buy_done_vs_planned(self):
        assert classify_treasury("자기주식 취득 결과보고서") == "buy_done"
        assert classify_treasury("자기주식 취득 결정") == "buy_planned"

    def test_dispose(self):
        assert classify_treasury("자기주식 처분 결과보고서") == "dispose_done"
        assert classify_treasury("자기주식 처분 결정") == "dispose_planned"

    def test_other(self):
        assert classify_treasury("기타 일반 공시") == "other"
        assert classify_treasury("") == "other"


class TestTreasuryScore:
    def test_burn_strongly_positive(self):
        assert treasury_score(["burn"]) == 1.0
        assert treasury_score(["burn", "burn"]) == 2.0

    def test_trust_cancel_negative(self):
        assert treasury_score(["trust_cancel"]) == -0.5

    def test_mixed(self):
        score = treasury_score(["buy_done", "trust_cancel", "dispose_done"])
        # +0.7 - 0.5 - 0.7 = -0.5
        assert abs(score - (-0.5)) < 1e-6


class TestClassifyGovernance:
    def test_split_merge(self):
        assert classify_governance_disclosure("회사분할결정") == "split_merge"
        assert classify_governance_disclosure("주요사항보고서(합병등종료보고서)") == "split_merge"

    def test_incident(self):
        assert classify_governance_disclosure("감사범위제한 한정의견") == "incident"
        assert classify_governance_disclosure("횡령·배임혐의발생") == "incident"

    def test_other(self):
        assert classify_governance_disclosure("기타 일반 공시") == "other"


class TestAgmContext:
    def test_returns_required_fields(self):
        ctx = agm_context()
        assert "next_agm" in ctx
        assert "proposal_deadline" in ctx
        assert "days_to_agm" in ctx
        assert "phase" in ctx
        assert ctx["phase"] in ("stake_building", "campaign_window", "agm_window", "post_agm")


class TestIndustryPeers:
    def test_auto_parts_303(self):
        peers = industry_peers("303", exclude_self="021820", limit=5)
        assert len(peers) <= 5
        assert "021820" not in peers   # exclude_self honored

    def test_no_match_returns_empty(self):
        peers = industry_peers("99999")
        assert peers == []

    def test_none_returns_empty(self):
        assert industry_peers(None) == []
