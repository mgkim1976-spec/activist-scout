# activist-scout

**KOSPI 행동주의 후보 스크리너 + IC급 단일 종목 deep dive 자동화**

> 💡 **이 프로젝트가 어떻게 만들어졌는지 → [STORY.md](STORY.md)**
>
> 한 줄짜리 종목 스크리닝 요청에서 시작해, [Claude Code](https://claude.ai/code) 와의
> 반복 대화로 IC급 행동주의 헤지펀드 분석 도구로 진화한 9일간의 여정.

---

## 무엇인가

KOSPI / KOSDAQ 가치주 중 **행동주의 헤지펀드의 캠페인이 기대수익을 낼 가능성이 높은 종목**
을 자동 발굴하고, 선택한 종목에 대해 PM 1시간 작업 수준의 IC 보고서를 ~30초에 생성합니다.

**입력**: 시장 데이터 (pykrx, yfinance, DART)
**출력**:
- `report.md` — 30~50개 universe 의 tier 분류 (HOT / WARM / WATCH / LATE_*  / AVOID)
- `deep_dive_<ticker>.md` — 단일 종목 13 섹션 IC급 보고서
- `monitor.py` — 일별 신규 5%+ filing 알림

## 누구를 위한 것

- 한국 가치투자·행동주의 펀드 PM 및 analyst
- 한국 주식 장기 투자자 중 가치 함정과 진짜 deep value 를 구분하고 싶은 사람
- 한국 거버넌스·5%+ 공시 데이터를 체계적으로 추적하고 싶은 연구자
- AI 보조 코딩으로 research-grade 도구를 만드는 패턴에 관심 있는 개발자

> ⚠️ **투자 권유 아님.** [DISCLAIMER.md](DISCLAIMER.md) 필독.

---

## 빠른 시작

### 설치

```bash
git clone https://github.com/your-org/activist-scout.git
cd activist-scout
pip install -e .
# 또는
pip install -r requirements.txt
```

Python 3.11+ 필요.

### 자격증명 설정

```bash
cp .env.example .env
# .env 파일에 4개 키 입력:
# - KRX_ID, KRX_PW (https://data.krx.co.kr/)
# - DART_API_KEY (https://opendart.fss.or.kr/)
# - OPENAI_API_KEY 또는 GEMINI_API_KEY
```

### 첫 실행

```bash
# 전체 파이프라인 (~10분)
python -m activist_scout.pipeline

# 또는 단계별로
python -m activist_scout.pipeline --only screen      # 1차 필터만
python -m activist_scout.pipeline --only score       # 점수 재계산
python -m activist_scout.pipeline --only report      # 리포트 재생성

# 단일 종목 IC 보고서 생성 (~30초)
python -m activist_scout.deep_dive 021820 --output reports/sewon.md
```

### 일별 cron (선택)

```bash
# crontab — 새 5%+ filing 발생 시 알림
0 9 * * * cd /path/to/activist-scout && python -m activist_scout.monitor
```

---

## 시스템 개요

3개의 파이프라인으로 구성됩니다:

```
                ┌─────────────────────────────────────┐
                │  corp_code map (분기 1회 갱신, 공유)  │
                └─────────────────┬───────────────────┘
                                  │
            ┌─────────────────────┴────────────────────┐
            ▼                                          ▼
    ┌──────────────────┐                    ┌──────────────────┐
    │   BACKTEST       │                    │   SCREENING      │
    │  (분기 1회)       │                    │  (주간/월간)      │
    │                  │                    │                  │
    │ KOSPI 전체 10년   │                    │ 오늘의 universe   │
    │ 5%+ filing →     │                    │ 8 stage 필터·분류  │
    │ KOSPI alpha      │                    │      ↓           │
    │ 분포 측정         │                    │ report.md +       │
    │                  │                    │ scores.csv        │
    └──────────────────┘                    └─────────┬────────┘
                                                      │
                                            ┌─────────┴────────┐
                                            ▼                  ▼
                                    ┌──────────────┐  ┌──────────────┐
                                    │  deep_dive   │  │   monitor    │
                                    │  단일 종목    │  │  (일별 cron)  │
                                    │  IC 보고서    │  │  신규 5%+    │
                                    └──────────────┘  └──────────────┘
```

### Screening pipeline 8 단계

| 단계 | 역할 | 출력 |
|---|---|---|
| 1. screen | PBR ≤ 0.8 + 영업이익 + 매출 + 오너지분 + 잉여자본 | `screening_value_ownership.csv` |
| 2. fetch_flow | 기관 90D/20D 순매수 + 매수 VWAP | `institutional_flow.csv` |
| 3. fetch_liq | ADV 20D + 5% 매집 소요일 + capacity | `liquidity.csv` |
| 4. enrich | DART 자사주·5%+·거버넌스·임원·자회사 | `enriched.json` |
| 5. parse_rp | LLM 으로 일감몰아주기 비중 정량화 | `enriched.json` (필드 추가) |
| 6. classify | Gemini 7-카테고리 정성 분류 | `classification.{json,csv}` |
| 7. score | 3축 룰베이스 점수 + tier 분류 + Captive 식별 | `scores.{json,csv}` |
| 8. report | tier 별 종목 카드 markdown | `report.md` |

### 3축 점수 시스템

| 축 | 의미 | 핵심 룰 |
|---|---|---|
| **TARGET_ATTRACTIVENESS** | 행동주의가 매력 느낄 만한가 | 잉여자본 / 오너 sweet spot / NAV 디스카운트 / 자사주 우호 |
| **ACCUMULATION_SIGNATURE** | 이미 매집 중인가 | 90D 매수일 비율 / 누적 net buy / VWAP 안정성 / secrecy |
| **LEGAL_VULNERABILITY** | post-amendment 상법 leverage 큰가 | 자사주 처분·신탁해지 / 분할·합병 / 거버넌스 사고 / 일감몰아주기 / 사외이사 임기 |

### Tier 분류 (보고서 우선순위 순)

| Tier | 조건 | 의미 |
|---|---|---|
| HOT | 3축 모두 ≥ 60 | 모든 차원 강함 |
| WARM | 2축 ≥ 60 | 2개 축 강함 |
| **LATE_SKEPTICAL** | 5%+ 공개 + alpha < −5% | **시장 회의적, 역설적 매수 기회** |
| WATCH | 1축 ≥ 70 | 단일 축 압도적 |
| LATE_ACCESSIBLE | 5%+ 공개 + alpha −5% ~ +20% | 시장 미온적, 잔여 차익 |
| LATE_PRICED_IN | 5%+ 공개 + alpha ≥ +20% | 정보 우위 소진 |
| LATE | 5%+ 공개 + filing < 7일 | alpha 측정 불가 |
| PASS | 모든 축 미달 | 현재 무신호 |
| AVOID | 최대주주 ≥ 65% 또는 captive | 행동주의 무력화 |

---

## 단일 종목 deep dive

선택한 종목에 대해 13 섹션 IC급 markdown 보고서 자동 생성:

```bash
python -m activist_scout.deep_dive 021820 \
    --context-file context/sewon.txt \
    --output reports/sewon_deep_dive.md
```

생성되는 섹션:

1. PM Thesis (1줄)
2. 회사 개요 + 산업 컨텍스트
3. 펀더멘털 스냅샷 (qoq cross-check)
4. 수익성 (ROE / CoE gap / FCF yield / EV/OI)
5. Sum-of-parts NAV (자회사 implied uplift)
6. 상장 자회사 직접 분석
7. Peer comparison (동종 PBR median ranking)
8. 잉여자본 정량
9. 최대주주 구조 + 외부 5%+ 추세
10. 외국인 지분 추세
11. 거버넌스 record + 임원 보수 alignment + 자사주 5Y history
12. Catalyst Timing
13. 큰 공시 자동 분석
14. 일감몰아주기 정량 + captive 추론
15. 진입 권고 점수 매트릭스 + Catalyst probability × impact
16. 운영 plan (1.5% vs 0.5% 시장임팩트 비교)
17. 종합 결론 + PnL impact + Workflow cadence
18. ⚠️ 자동화 외 PM 영역 (사람 검증 필수)

LLM: `gpt-5.4-mini` (default, `.env` 의 `OPENAI_MODEL` 로 변경 가능).

---

## 핵심 컬럼·메트릭

- **잉여자본비율**: (현금 + 단기투자 − 총부채) / 시가총액. > 0.30 이면 행동주의 핵심 타깃
- **NAV 디스카운트**: (시총 − Sum-of-parts) / NAV × 100. 음수 = 시총 < NAV
- **NAV trust**: 상장 자회사 비중 ≥ 50% = HIGH, < 25% = LOW (premium 시그널 무시)
- **capacity_score**: 5% 매집 ≤ 30일 = 1.0, ≥ 90일 = 0.0
- **treasury_score**: 자사주 공시 가중합. 양수 = 우호적 (소각/매입), 음수 = 비우호적
- **filer_type**: activist / semi_activist / passive / pe_fund / strategic / individual
- **AVOID 면제**: 오너 60~65% + (NAV 디스카운트 ≤ −30% OR treasury_score ≥ +1.0)
- **Captive 식별**: 일감 ≥ 50% + 모회사 strategic 지분 ≥ 50% → 자동 AVOID

---

## 문서

- [STORY.md](STORY.md) — 프로젝트 9일간의 진화 이야기
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md) — 방법론 상세 (점수 가중치 근거 등)
- [DISCLAIMER.md](DISCLAIMER.md) — 면책 조항
- [LICENSE](LICENSE) — MIT

---

## 디렉토리 구조

```
activist-scout/
├── README.md                         # 이 파일
├── STORY.md                          # 프로젝트 진화 이야기
├── DISCLAIMER.md                     # 면책 조항
├── LICENSE                           # MIT
├── pyproject.toml                    # Python 패키징
├── requirements.txt                  # pip 의존성
├── .env.example                      # 자격증명 템플릿
├── src/
│   └── activist_scout/               # 메인 패키지
│       ├── config.py                 # 자격증명·경로·상수
│       ├── domain.py                 # 행동주의 화이트리스트·도메인 룰
│       ├── utils.py                  # KRX retry·DART 클라이언트
│       ├── pipeline.py               # 오케스트레이터
│       ├── monitor.py                # 일별 신규 filing 알림
│       ├── deep_dive.py              # 단일 종목 IC 보고서
│       └── stages/                   # 8 파이프라인 stage
│           ├── screen.py
│           ├── fetch.py
│           ├── enrich.py
│           ├── related_party.py
│           ├── classify.py
│           ├── score.py
│           ├── report.py
│           ├── backtest.py
│           └── validate.py
├── tools/
│   └── scan_rerate.py                # 역사적 PBR 리레이팅 탐색 (별도)
├── docs/
│   └── METHODOLOGY.md
├── tests/                            # pytest
├── examples/                         # 예시 출력
└── data/                             # 실행 산출물 (gitignored)
```

---

## 운영 / 유지보수

```bash
# 매주 (universe 갱신)
python -m activist_scout.pipeline

# 주요 후보 deep dive (~30초/종목)
python -m activist_scout.deep_dive <ticker>

# 일별 cron (5%+ filing alert)
python -m activist_scout.monitor

# 분기 1회 (calibration prior 갱신)
python -m activist_scout.pipeline --pipeline backtest

# domain.py 의 행동주의 펀드 화이트리스트 분기 update
```

---

## 한계와 정직성

이 시스템이 **할 수 없는** 것은 [DISCLAIMER.md](DISCLAIMER.md) 와 모든 deep dive 보고서
§13 ("자동화 외 PM 영역") 에 명시:

- 공시 본문 직접 read (파일명만 파싱)
- 외국계 행동주의 펀드 한국 입국 신호
- 매니지먼트 인터뷰
- 노조 stance / 산업 가십
- IC 동료 PM·법률 자문 review
- 부동산 공시지가 vs 장부가 (외부 데이터 필요)

**가장 위험한 실패 양상**: 시스템이 *마치* 결정을 대신해 주는 것처럼 보이는 것. fork
시 §13 정직성 유지 부탁드립니다.

---

## 기여 / 라이선스

MIT 라이선스. Pull request 환영. 특히:
- `domain.py` 의 행동주의 펀드 화이트리스트 갱신
- `tools/` 의 추가 분석 스크립트
- `tests/` 보강
- 새 데이터 소스 통합
