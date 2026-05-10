"""filing_intel — DART 5%+ 대량보유보고서 본문 → catalyst 발굴 자동화 prototype.

수동 prototype 단계:
  사용자(PM) 와의 한 세션 (2026-05-11) 에서 두올(016740) 인수 거래 분석을
  DART document.xml + Gemini structured output + Google search grounding 으로
  재구성한 흐름을 자동화. activist-scout 의 행동주의 후보 universe 너머
  *모든 5%+ 신고* 를 입력으로 받아 시나리오 (행동주의/M&A/PE buyout 등) 별로
  분류한다.

Phase 1 (현 위치 tools/filing_intel/): MVP, 두올 케이스 재현 검증.
Phase 2: 검증 후 별도 repo (5pct-radar) 로 분리 예정.

⚠️ 본 모듈도 §13 "사람 검증 필수" 정신을 유지한다. LLM 출력은 *반드시
원본 공시 본문으로 검증* 후 의사결정. 환각 가능성 명시.
"""
