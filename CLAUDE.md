# activist-scout — Claude 작업 가이드

이 파일은 Claude Code 가 새 세션을 시작할 때 자동으로 로드됩니다. 프로젝트 컨텍스트를
즉시 회복하는 용도입니다.

---

## 1. 프로젝트 한 줄 요약

한국 거래소 상장 종목 중 **행동주의 펀드 캠페인 후보** 를 자동 발굴하고, 한 종목씩
**펀드 매니저급 분석 보고서** 를 약 30초에 자동 생성하는 도구.

**현재 상태**: v0.1.0 public 공개 (2026-05-09)
**저장소**: https://github.com/mgkim1976-spec/activist-scout

---

## 2. 새 세션 빠른 컨텍스트 회복

작업을 이어 받기 전에 이 순서로 읽어 주세요:

1. **[STORY.md](STORY.md)** — 9일간의 모든 핵심 결정 narrative. 필독.
2. **[README.md](README.md)** — 사용자용 가이드 (초보자 친화)
3. **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)** — 점수 가중치 근거 (필요 시)

---

## 3. 사용자 선호 (절대 잊지 말 것)

### 응답 언어
- **한국어 전용.** 영어 응답 금지.
- 한국어 안에 영어 약자가 자연스러우면 사용 가능 (예: `pip install`, 변수명).

### 외부 노출 문서의 약자/전문용어
- README/STORY/CHANGELOG 같은 **GitHub 에 보이는 문서** 에서는 약자 풀어쓰기:
  - IC → "투자 결정 회의" / "펀드 매니저급"
  - PM → "펀드 매니저"
  - DART → "금융감독원 전자공시(DART)"
  - PBR/PER → "자산/이익 대비 주가가 싼"
  - NAV → "자산 합산 가치"
  - LLM → "인공지능"
  - regime change → "법이 바뀌어 환경이 달라진 것"
  - LATE_SKEPTICAL → "시장 회의적 (역설적 매수 기회)"
- *코드 내부 변수명·함수명*은 영어 그대로 유지 (기술적 정체성).

### 인공지능 출력 검증
- 인공지능이 생성한 내용은 *반드시 원본 데이터로 검증* 후 보고.
- 환각 가능성 명시. "AI 가 그렇게 답했다" → "AI 답변을 직접 검증한 결과" 로.

---

## 4. 절대 받지 않는 변경 (사용자 가장 중요시)

1. **§13 "사람 검증 필수" 섹션 제거** — 모든 deep dive 보고서 마지막. 시스템 정직성
   핵심. *"가장 위험한 함정은 시스템이 마치 의사 결정을 대신해 주는 것처럼 보이는 것"*
2. **과거 데이터로 미래 수익 예측한다는 주장** — 한국 상법 개정으로 regime change.
   어떤 calibration·확률 주장도 안 함.
3. **투자 권유성 텍스트** — "이 종목 사세요" 류 절대 금지.
4. **`.env` 또는 비밀 정보 commit** — `.gitignore` 에서 자동 제외 중. 확인 필요.

---

## 5. 명령어 빠른 참조

```bash
# 후보 종목 목록 만들기 (~10분)
python -m activist_scout.pipeline

# 한 종목 심층 보고서 (~30초)
python -m activist_scout.deep_dive <종목코드>

# 매일 새 5%+ 신고 알림
python -m activist_scout.monitor

# 분기 과거 검증
python -m activist_scout.pipeline --pipeline backtest

# 테스트
pytest tests/

# 코드 검사
ruff check src tests
```

---

## 6. 아키텍처 핵심

```
8단계 파이프라인:
  screen → fetch → enrich → related_party → classify → score → report → backtest

3축 점수:
  - 타깃 매력도 (TARGET_ATTRACTIVENESS)
  - 매집 흔적 (ACCUMULATION_SIGNATURE)
  - 법적 지렛대 (LEGAL_VULNERABILITY)

9가지 등급:
  HOT (강한 후보) → WARM (유망) → LATE_SKEPTICAL (시장 회의적, 역설적 기회)
  → WATCH (관심) → LATE_ACCESSIBLE (시장 미온적) → LATE_PRICED_IN (이미 반영됨)
  → LATE (신호 신선) → PASS (관망) → AVOID (피해야 함)

AVOID 면제 룰:
  최대주주 60~65% + (NAV 할인 ≤ -30% OR treasury_score ≥ +1.0)

Captive 자동 식별:
  일감 ≥ 50% + 모회사 지분 ≥ 50% → 자동 AVOID
```

---

## 7. 핵심 코드 위치

| 모듈 | 역할 | 줄 수 |
|---|---|---|
| `src/activist_scout/config.py` | 자격증명·경로·점수 가중치 | ~200 |
| `src/activist_scout/domain.py` | 행동주의 펀드 명단, classifier | ~300 |
| `src/activist_scout/deep_dive.py` | 단일 종목 IC 보고서 자동화 | **~1300** |
| `src/activist_scout/pipeline.py` | 3 pipeline 오케스트레이터 | ~150 |
| `src/activist_scout/stages/*.py` | 8 stage 각각 분리 | ~150씩 |

---

## 8. 데이터 디렉토리

`data/` 는 `.gitignore` 에서 자동 제외 — clone 시 비어 있음. 실행 산출물 위치:

- `data/screening_value_ownership.csv` — 1차 필터 통과 종목
- `data/enriched.json` — DART 공시 + 자회사 데이터
- `data/classification.{json,csv}` — Gemini 7카테고리 분류
- `data/scores.{json,csv}` — 3축 점수 + tier
- `data/report.md` — 최종 후보 목록
- `data/deep_dive_<종목코드>.md` — 한 종목 심층 보고서

---

## 9. 자격 증명 (`.env`)

```
KRX_ID=...        # https://data.krx.co.kr
KRX_PW=...
DART_API_KEY=...  # https://opendart.fss.or.kr
OPENAI_API_KEY=...  # 또는
GEMINI_API_KEY=...
```

`.env.example` 템플릿 제공. `.env` 는 절대 commit 금지.

---

## 10. 이전 세션 transcript

이 프로젝트의 처음 9일간 전체 대화 기록:

`~/.claude/projects/-Users-mg-mac-MGPrj-screening/7b74d7a0-58db-420e-b9d7-4359b159749b.jsonl`

상세 결정 사유가 필요할 때 참조 (단, 대부분은 STORY.md 에 정리됨).
