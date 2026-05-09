# Changelog

본 프로젝트의 모든 주요 변경 사항을 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)
형식으로 기록합니다.

자세한 *진화 이야기* 는 [STORY.md](STORY.md) 참조.

## [0.1.0] — 2026-05-09

### 첫 public release

- KOSPI 행동주의 후보 스크리너 (8 stage 파이프라인)
- 단일 종목 IC급 deep dive 자동화 (13 섹션)
- 일별 5%+ filing 알림 (`monitor.py`)
- 분기 backtest (KOSPI alpha 통제)
- 3축 룰베이스 점수 + tier 분류 (HOT/WARM/WATCH/LATE_*/AVOID)
- LATE sub-classification (PRICED_IN / ACCESSIBLE / SKEPTICAL)
- AVOID 면제 룰 (오너 60~65% + NAV/treasury 우호)
- Captive subsidiary 자동 식별
- 사외이사 임기 catalyst timing
- Sum-of-parts NAV (자회사 implied 4~5배 uplift)
- LLM 기반 일감몰아주기 정량화 (DART 사업보고서 §VIII 파싱)
- 12 helper: ROE/CoE, FCF yield, peer comparison, 외국인 추세, 임원 보수,
  자사주 5Y history, 5Y 주가, catalyst probability matrix, etc.

### 라이선스 / 거버넌스

- MIT License
- DISCLAIMER 명시 (투자 권유 아님)
- §13 "자동화 외 PM 영역" 모든 deep dive 보고서에 의무 포함
