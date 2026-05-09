"""
도메인 지식 모듈: 행동주의 펀드 화이트리스트, 자사주 공시 방향성 분류, AGM 타임라인.

이 파일이 룰의 single source of truth — 실무자가 정기 업데이트하는 곳.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

# ─────────────────────────────────────────────────────────────────────────────
# 1) 행동주의 / 패시브 / 사모 펀드 분류
# ─────────────────────────────────────────────────────────────────────────────

# 한국에서 명시적 캠페인 이력이 있거나 5%+ 보고 후 경영참여 행보가 잦은 펀드.
# 신규 5%+ 보고 = 강한 캠페인 시그널.
HARDCORE_ACTIVISTS = {
    "차파트너스자산운용", "차파트너스",
    "얼라인파트너스자산운용", "얼라인파트너스", "얼라인",
    "안다자산운용",
    "트러스톤자산운용", "트러스톤",
    "이루다투자일임",
    "VIP자산운용", "브이아이피자산운용",
    "메리츠자산운용",
    "라이프자산운용",
    "플래쉬라이트캐피탈코리아",
    "돌체인베스트먼트",
    "KCGI", "케이씨지아이", "KCGI자산운용",
    "네버다이어셋매니지먼트", "네버다이",
    # 외국계
    "Dalton Investments", "Petrus Advisers",
    "Strategic Value Partners", "Elliott", "Engine No. 1",
}

# 가끔 캠페인을 하지만 대부분 가치투자형
SEMI_ACTIVISTS = {
    "베어링자산운용", "한국투자밸류자산운용",
    "신영자산운용", "에셋플러스자산운용",
    "Petra Capital", "FCP파트너스",
}

# 5%+ 신고는 잦지만 경영참여는 거의 안 함. 매수 시 행동주의 시그널 아님.
PASSIVE_DOMESTIC: set[str] = {
    "국민연금공단", "국민연금",
    "KB자산운용", "케이비자산운용",
    "삼성자산운용",
    "미래에셋자산운용",
    "한국투자신탁운용", "한국투자",
    "NH아문디자산운용", "NH아문디",
    "키움투자자산운용",
    "신한자산운용",
    "하나자산운용",
    "우리자산운용",
    "DB자산운용",
}

PASSIVE_FOREIGN: set[str] = {
    "BlackRock", "블랙록",
    "Vanguard", "뱅가드",
    "State Street", "스테이트 스트리트",
    "JPMorgan", "JP모건",
    "Morgan Stanley",
    "Goldman Sachs",
    "Capital Group", "캐피탈 그룹",
}

# 매칭 편의를 위해 통합 set 유지 (위 두 set의 합집합)
PASSIVE: set[str] = PASSIVE_DOMESTIC | PASSIVE_FOREIGN

FilerType = Literal["activist", "semi_activist", "passive", "pe_fund",
                    "strategic", "individual", "unknown"]


def classify_filer(repror: str) -> FilerType:
    """5%+ 보고자 이름을 기관 유형으로 분류.

    우선순위: activist > semi_activist > passive > pe_fund > strategic > individual.
    """
    if not repror:
        return "unknown"
    nm = repror.strip()

    if any(a in nm for a in HARDCORE_ACTIVISTS):
        return "activist"
    if any(a in nm for a in SEMI_ACTIVISTS):
        return "semi_activist"
    if any(p in nm for p in PASSIVE):        # PASSIVE_DOMESTIC | PASSIVE_FOREIGN 통합
        return "passive"

    # 키워드 기반 보조 분류
    if "사모투자" in nm or "사모펀드" in nm or "투자조합" in nm or "PEF" in nm.upper():
        return "pe_fund"

    # 그룹/계열사 (보유 ≥ 5% 인 경우 보통 strategic — 모회사/자회사/특수관계인)
    if ("(주)" in nm or "주식회사" in nm) and "자산운용" not in nm and "투자" not in nm:
        return "strategic"

    # 사람 이름으로 보이는 경우 (한글 2~4자 + 한자/영문 미포함)
    if 2 <= len(nm) <= 4 and all('가' <= ch <= '힯' for ch in nm):
        return "individual"

    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 2) 자사주 공시 방향성 분류
# ─────────────────────────────────────────────────────────────────────────────

TreasuryDir = Literal[
    "burn",              # 자사주 소각 — 가장 강한 positive
    "buy_done",          # 취득 결과보고 — positive (실집행)
    "buy_planned",       # 취득 결정 — positive (계획)
    "trust_open",        # 신탁계약 체결 — mild positive
    "trust_extend",      # 신탁계약 연장 — mild positive
    "trust_cancel",      # 신탁계약 해지 — negative (계획 취소)
    "dispose_done",      # 처분 결과보고 — negative
    "dispose_planned",   # 처분 결정 — negative
    "other",
]


# 행동주의 관점 가중치. EV 계산용.
TREASURY_WEIGHT: dict[TreasuryDir, float] = {
    "burn": 1.0,
    "buy_done": 0.7,
    "buy_planned": 0.4,
    "trust_open": 0.3,
    "trust_extend": 0.2,
    "trust_cancel": -0.5,
    "dispose_planned": -0.4,
    "dispose_done": -0.7,
    "other": 0.0,
}


def classify_treasury(report_nm: str) -> TreasuryDir:
    """자사주 관련 공시명을 방향성 카테고리로 분류."""
    if not report_nm:
        return "other"
    s = report_nm

    # 가장 구체적인 패턴부터 (우선순위 중요)
    if "소각" in s:
        return "burn"

    # 신탁: 해지 vs 체결/연장
    if "신탁" in s:
        if "해지" in s:
            return "trust_cancel"
        if "연장" in s:
            return "trust_extend"
        if "체결" in s or "취득" in s:
            return "trust_open"
        return "trust_open"

    # 처분 vs 취득
    if "처분" in s:
        return "dispose_done" if "결과" in s else "dispose_planned"
    if "취득" in s:
        return "buy_done" if "결과" in s else "buy_planned"

    return "other"


def treasury_score(directions: list[TreasuryDir]) -> float:
    """방향성 가중치 합. 양수면 자사주 정책 우호적."""
    return sum(TREASURY_WEIGHT.get(d, 0) for d in directions)


# ─────────────────────────────────────────────────────────────────────────────
# 3) 정기주주총회 타임라인
# ─────────────────────────────────────────────────────────────────────────────

AGMPhase = Literal["stake_building", "campaign_window", "agm_window", "post_agm"]


def agm_context(today: datetime | None = None) -> dict:
    """다음 정기주주총회와 주주제안 마감일.

    한국 KOSPI 정기주총: 보통 3월 마지막 주.
    주주제안 마감: 주총일 6주 전.
    상장규정상 의안 통지 = 14일 전, 행동주의는 6주 전 제안 권장.

    phase:
      - stake_building: 마감일까지 60일 초과 → 조용히 매집
      - campaign_window: 마감일까지 0~60일 → 캠페인 가능
      - agm_window: 마감일 지났지만 주총일 전 → 표결 결과 대기
      - post_agm: 주총 직후 ~ 다음 stake_building 전
    """
    today = today or datetime.now()
    year = today.year
    next_agm = datetime(year, 3, 31)
    if today >= next_agm:
        next_agm = datetime(year + 1, 3, 31)

    proposal_deadline = next_agm - timedelta(weeks=6)
    days_to_agm = (next_agm - today).days
    days_to_deadline = (proposal_deadline - today).days

    if days_to_deadline > 60:
        phase = "stake_building"
    elif days_to_deadline > 0:
        phase = "campaign_window"
    elif days_to_agm > 0:
        phase = "agm_window"
    else:
        phase = "post_agm"

    return {
        "today": today.strftime("%Y-%m-%d"),
        "next_agm": next_agm.strftime("%Y-%m-%d"),
        "proposal_deadline": proposal_deadline.strftime("%Y-%m-%d"),
        "days_to_agm": days_to_agm,
        "days_to_deadline": days_to_deadline,
        "phase": phase,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4) Post-amendment 상법 leverage — 분할/거버넌스/감사 공시 키워드 분류
# ─────────────────────────────────────────────────────────────────────────────

# 분할/합병/M&A — post-amendment 상법에서 소수주주 권리 강화
SPLIT_MERGE_KEYWORDS = (
    "분할",        # 인적분할/물적분할
    "합병",        # 합병결정
    "주식교환",    # 포괄적 주식교환
    "주식이전",
    "영업양수",
    "영업양도",
)

# 거버넌스 사고 — 신뢰 록인의 raw signal
GOVERNANCE_INCIDENT_KEYWORDS = (
    "감사범위제한",
    "감사의견거절",
    "한정의견",
    "거래정지",
    "상장폐지",
    "관리종목",
    "투자주의환기",
    "횡령",
    "배임",
    "부정거래",
    "분식회계",
)

# 일감몰아주기 / 특수관계인 거래 — 사업보고서 §X 텍스트에서 검색
RELATED_PARTY_KEYWORDS = (
    "특수관계자거래",
    "특수관계인거래",
    "일감몰아주기",
)


GovernanceTag = Literal["split_merge", "incident", "related_party", "other"]


# ─────────────────────────────────────────────────────────────────────────────
# 산업별 peer 종목 (KSIC 2~3자리 → 대표 KOSPI 종목)
# 실무 PM이 valuation 비교 시 사용하는 동종사. 분기 1회 update 권장.
# ─────────────────────────────────────────────────────────────────────────────

INDUSTRY_PEERS: dict[str, list[str]] = {
    # 자동차 부품 (induty 30, 303 등)
    "30": ["012330", "204320", "018880", "010690", "064960", "010770",
           "204210", "011210", "067900", "032280", "021820", "002350"],
    "303": ["012330", "204320", "018880", "010690", "064960", "010770",
            "021820", "200880", "378850", "013870"],
    # 음식료품
    "10": ["004370", "001680", "248170", "007540", "006090", "003960",
           "264900", "005740", "101530", "005180", "027740", "079160"],
    "11": ["003920", "000080", "005300", "271560"],
    # 화학
    "20": ["051910", "010060", "011170", "298050", "069260"],
    # 제약·바이오
    "21": ["207940", "068270", "326030", "000100", "002390"],
    # 1차금속
    "24": ["005490", "000760", "002240", "002710"],
    # 금속가공 (자동차부품 일부 포함)
    "25": ["010780", "024090", "067990"],
    # 전자/IT
    "26": ["005930", "000660", "066570", "034220", "009150"],
    # 전기장비
    "28": ["010120", "011070", "267260"],
    # 일반기계
    "29": ["042660", "010620", "180640"],
    # 건설
    "41": ["000720", "047040", "028260", "006360", "375500"],
    # 운송
    "49": ["180640", "044820"],
    # IT 서비스
    "62": ["018260", "009440", "035000", "036530"],
    # 금융
    "64": ["105560", "316140", "086790", "139130"],
    # 부동산·리츠
    "68": ["330590", "330600", "395400"],
    # 리스/렌탈
    "76": ["089860"],
}


def industry_peers(induty_code: str | None, exclude_self: str | None = None,
                   limit: int = 8) -> list[str]:
    """induty_code → 동종 KOSPI peer ticker list (자기 자신 제외)."""
    if not induty_code:
        return []
    s = str(induty_code)
    candidates = []
    if s in INDUSTRY_PEERS:
        candidates = INDUSTRY_PEERS[s][:]
    elif s[:2] in INDUSTRY_PEERS:
        candidates = INDUSTRY_PEERS[s[:2]][:]
    if exclude_self:
        candidates = [c for c in candidates if c != exclude_self]
    return candidates[:limit]


def classify_governance_disclosure(report_nm: str) -> GovernanceTag:
    """공시명에서 분할/거버넌스 사고 분류. 자사주는 별도 (classify_treasury)."""
    if not report_nm:
        return "other"
    s = report_nm
    if any(kw in s for kw in GOVERNANCE_INCIDENT_KEYWORDS):
        return "incident"
    if any(kw in s for kw in SPLIT_MERGE_KEYWORDS):
        return "split_merge"
    if any(kw in s for kw in RELATED_PARTY_KEYWORDS):
        return "related_party"
    return "other"


# ─────────────────────────────────────────────────────────────────────────────
# 5) Self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # filer
    cases = [
        ("브이아이피자산운용", "activist"),
        ("KB자산운용", "passive"),
        ("국민연금공단", "passive"),
        ("프리미어성장전략엠앤에이사모투자합자회사", "pe_fund"),
        ("(주)모트렉스이에프엠", "strategic"),
        ("김종석", "individual"),
        ("BlackRock Inc.", "passive"),
    ]
    print("== filer classification ==")
    for nm, exp in cases:
        got = classify_filer(nm)
        mark = "✓" if got == exp else "✗"
        print(f"  {mark} {nm:40s} → {got} (expected {exp})")

    # treasury
    print("\n== treasury classification ==")
    for nm, exp in [
        ("주요사항보고서(자기주식취득결정)", "buy_planned"),
        ("주요사항보고서(자기주식취득신탁계약해지결정)", "trust_cancel"),
        ("자기주식 취득 결과보고서", "buy_done"),
        ("자기주식 처분 결정", "dispose_planned"),
        ("자기주식 소각 결정", "burn"),
        ("자기주식 처분 결과보고서", "dispose_done"),
    ]:
        got = classify_treasury(nm)
        mark = "✓" if got == exp else "✗"
        print(f"  {mark} {nm:40s} → {got} (expected {exp})")

    # AGM
    print("\n== AGM ==")
    print(agm_context())

    # Governance
    print("\n== governance classification ==")
    for nm, exp in [
        ("회사분할결정", "split_merge"),
        ("주요사항보고서(합병등종료보고서)", "split_merge"),
        ("감사범위제한 한정의견", "incident"),
        ("횡령·배임혐의발생", "incident"),
        ("기타 일반 공시", "other"),
    ]:
        got = classify_governance_disclosure(nm)
        mark = "✓" if got == exp else "✗"
        print(f"  {mark} {nm:40s} → {got} (expected {exp})")
