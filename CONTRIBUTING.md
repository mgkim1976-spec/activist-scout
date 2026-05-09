# 기여 가이드

`activist-scout` 에 기여를 환영합니다. 다음 영역이 특히 가치 있습니다.

## 환영하는 기여

### 1. 도메인 지식 갱신
- `src/activist_scout/domain.py` 의 행동주의 펀드 화이트리스트 추가/업데이트
- `INDUSTRY_PEERS` 동종 종목 매핑 정확도 개선
- `INDUSTRY_CONTEXT` 산업별 행동주의 컨텍스트 보강

### 2. 데이터 소스 확장
- 한국 부동산 공시지가 API 통합
- 외국계 행동주의 펀드 한국 진입 모니터링
- 추가 DART endpoint 활용 (회계감사인 변경, 공정거래위원회 제재 등)

### 3. 테스트 보강
- `tests/` 에 새 테스트 케이스 추가
- 특히 도메인 룰의 edge case

### 4. 문서 / 예시
- 사용 사례 추가 (`examples/` 디렉토리)
- 한국 행동주의 캠페인 case study 추가

## 기여 절차

1. Fork
2. Feature branch 생성: `git checkout -b feature/your-feature`
3. 변경 + 테스트
4. Lint 통과 확인: `ruff check src tests`
5. 테스트 통과 확인: `pytest tests/`
6. Commit + Push
7. Pull Request

## 코드 스타일

- Python 3.11+
- `ruff check` 통과
- 모든 public 함수에 docstring (한국어 또는 영어)
- 도메인 룰 변경 시 `STORY.md` 또는 `CHANGELOG.md` 에 *왜* 변경했는지 기록

## 절대 PR 받지 않는 것

1. `§13 ⚠️ 자동화 외 PM 영역` 섹션 *제거* — 이건 시스템의 정직성 핵심
2. *Calibration 데이터로 미래 수익 예측* 류 (regime change 무시)
3. **투자 권유성 텍스트** ("이 종목 사세요" 류)
4. .env 또는 secrets 의 commit

## 문제 / 제안

- Issues: GitHub Issues
- 예시 / 분석 결과 공유: Discussions

[STORY.md](STORY.md) 의 정신 — *대화로 발견하는 software* — 을 함께 이어가 주세요.
