# 방법론 — KOSPI 행동주의 후보 스크리닝 (v7)

> **v5**: regime change (상법 개정) 때문에 historical Beta-binomial calibration 폐기,
> first-principles 룰베이스 3축 점수로 전환.
>
> **v6**: Sum-of-parts NAV (자회사 시총 매핑) + 일감몰아주기 structural proxy +
> sanity check + stake_secrecy 추가.
>
> **v6.1**: LLM (Gemini) 기반 일감몰아주기 정량화 (사업보고서 §X 본문 파싱) +
> AVOID 60→65 + 면제 조건 (NAV 디스카운트 / treasury_score).
>
> **v7**: NAV trust score (P3) + Captive subsidiary 자동 식별 (P2) +
> 사외이사 임기 만료 catalyst timing (P1) + Causal validation framework (P4).

이 문서는 현행 파이프라인(**v7**, v0.1.0 공개 기준)의 설계 의도, 각 메트릭의
정의, 임계값 근거, 한계, 그리고 운영 시 주의사항을 정리합니다. 코드 리뷰 /
모델 검토 / 신규 운용역 온보딩에 사용하세요.

---

## 1. 문제 정의

### 1.1 우리가 풀려는 문제

> *KOSPI 가치주(저PBR) 중에서 단순한 가치 함정이 아니라, 행동주의 헤지펀드의
> 캠페인이 가치를 unlock할 가능성이 있는 종목을 자동 식별*

행동주의 캠페인의 본질: **자본 배분이 비효율적인 회사**에 외부 압력을 가해
*잉여 자본의 주주환원*을 강제하고, 그 과정에서 PBR이 0.4 → 0.8~1.0으로 재평가.

### 1.2 가치 함정과의 차이

|  | 가치 함정 | 행동주의 후보 |
|---|---|---|
| 저PBR 원인 | 구조적 록인 (지배구조/산업) | 자본 배분 실패 |
| 시간 | 무기한 | 12~24개월 캠페인 사이클 |
| 촉매 | 산업 사이클 회복(가능성↓) | 5%+ filing → 주주제안 → AGM |
| 진입 후 | 주가 정체, IRR ≈ 배당+α | P × upside − (1−P) × downside |

### 1.3 한국 시장 특수성

- **3월 정기주총** — 캠페인 사이클이 강한 계절성을 가짐
- **5%+ 대량보유공시** (자본시장법 §147~149): 행동주의의 1차 evidence
- **밸류업** 정책 (2024~) — 자사주 소각·배당정책을 정부가 push
- **오너지분 60%+** 회사는 사실상 행동주의 무력화 — 40~55% sweet spot

---

## 2. 데이터 소스

| 소스 | 용도 | 한계 |
|---|---|---|
| **pykrx** | 일별 PBR/PER/시총, 기관 거래대금, OHLCV | KRX 로그인 필요, 멀티스레드 시 세션 충돌 |
| **yfinance** | 분기 영업이익, 연 매출, 대차대조표(현금/부채) | 한국 종목 일부 누락 (특히 KOSDAQ 소형주) |
| **DART OpenAPI** | 자사주/5%+/임원/회사개요 공시 | 분기 갱신 (1~2개월 시차) |
| **Gemini 2.5 Pro** | 분류 + narrative + 페이오프 추정 | 학습 컷오프 후 데이터 미반영, 환각 위험 |

---

## 3. 시스템 구조 — **2개 파이프라인**

시스템은 운영 주기와 데이터 흐름이 다른 두 파이프라인으로 구성됩니다.

### 3.1 Backtest pipeline — 분기 1회, calibration prior 생성

**목적**: KOSPI 전체 10년 5%+ filing의 KOSPI alpha 분포를 산출하여 *calibration source*를 만든다.
빠르게 변하지 않으므로 분기 1회 실행.

```
┌────────────────────────┐
│   build_corp_code      │ DART 종목코드 매핑 (utils.py)
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  backtest_activist.py  │ ① DART list.json 으로 KOSPI 전 5%+ 공시 수집
│                        │ ② classify_filer 로 activist/semi 필터
│                        │ ③ pykrx PBR + KOSPI 인덱스 alpha 계산
│                        │ ④ 3M/6M/12M/24M/36M raw + alpha 분포
└────────────────────────┘
                           → backtest_activist.json (calibration source)
                           → backtest_activist.md (사람이 읽는 요약)
```

### 3.2 Screening pipeline — 주간/월간, 오늘의 universe 평가

**목적**: 오늘 시점의 KOSPI universe를 다단 필터링·분류·평가하여 actionable 리포트 생성.

```
┌────────────────────────┐
│   build_corp_code      │ (분기 1회 갱신, 양쪽 공유)
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  screen_value_ownership│ PBR ≤ 0.8 + 영업이익 3Q+ 흑자 + 매출 4Y 단조증가
│                        │ + 최대주주 ≥ 40% + 잉여자본비율
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  fetch.py --mode flow  │ 기관 90D/20D 순매수 + 매수 VWAP
│  fetch.py --mode liq   │ ADV 20D + 5% 매집 소요일 + capacity_score
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  enrich_dart           │ 자사주 공시(direction 라벨) + 5%+(filer_type 라벨)
│                        │ + 임원 변동 + 회사개요. summary 메트릭 추가
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  parse_related_party   │ DART 사업보고서 본문 → Gemini 정량화
│                        │ 일감몰아주기 비중(ratio_pct, confidence)
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  classify_traps        │ Gemini 7-카테고리 (정성) + 액션 + narrative
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  score_targets         │ 3축 룰베이스 점수(TARGET / ACCUM / LEGAL)
│                        │ + tier(HOT/WARM/LATE_*/WATCH/PASS/AVOID)
│                        │ + LATE_* 분리용 filing 후 KOSPI alpha
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  build_report          │ Markdown 리포트 (AGM·섹터·tier별 카드)
└────────────────────────┘
```

> **v5 메모 — calibrate 단계 폐기.** v4까지 존재하던 Beta-binomial calibration 단계는
> 상법 개정으로 인한 regime change 때문에 v5에서 제거되었습니다(상세: §4.8).
> 현행 파이프라인은 룰베이스 `score_targets` 가 calibrate 자리를 대체합니다.

### 3.3 두 파이프라인의 분리 이유

- **운영 주기 다름**: backtest는 30분 ~ 1시간, screening은 5~10분
- **데이터 변화 주기 다름**: 10년 백테스트 모집단은 분기 1회 갱신이면 충분
- **재실행 비용 분리**: 매주 screening할 때 backtest를 다시 돌릴 필요 없음
- **연결 없음 (v5 이후)**: v4까지 backtest 결과가 calibration prior 로 screening 에
  주입되었으나, regime change 로 calibrate 단계가 폐기된 이후 backtest 는
  *참고용 reference* 일 뿐 screening 출력에 영향을 주지 않음 (§4.8, §5).

**실행 방법** (모듈 호출 방식 — v0.1.0 패키지화 이후):
```bash
python -m activist_scout.pipeline                              # screen 파이프라인 8 stage (기본)
python -m activist_scout.pipeline --only report                # 리포트만 재생성
python -m activist_scout.pipeline --from score                 # score 부터 끝까지
python -m activist_scout.pipeline --skip parse_rp,classify     # 일부 stage 제외
python -m activist_scout.pipeline --pipeline backtest          # 분기 1회 백테스트
python -m activist_scout.pipeline --pipeline daily             # monitor 단독 실행
```

stage 이름 목록 (screen 파이프라인): `screen, fetch_flow, fetch_liq, enrich,
parse_rp, classify, score, report`.

---

## 4. 메트릭 정의 및 근거

### 4.1 1차 필터 (universe)

#### PBR ≤ 0.8
- **근거**: KOSPI 평균 PBR ≈ 1.0~1.1. 0.8 이하 = 시장 평균 -20% 이상 할인.
- **트레이드오프**: 0.5 이하로 좁히면 universe가 너무 줄고, 1.0까지 넓히면 행동주의가 불필요한 우량주가 다수 통과.

#### 영업이익 3분기 이상 흑자
- **근거**: 적자 회사는 행동주의 캠페인이 들어와도 자본환원 자체가 어려움.
- **데이터**: yfinance `Operating Income` (분기). 4년치 중 최근 3개 분기.

#### 매출 4년 단조증가 (= 3년 연속 YoY+)
- **근거**: 성장하는데 시장이 무시하는 회사를 찾는 것.

#### 최대주주+특수관계인 ≥ 40%
- **근거**:
  - 40~55% → **행동주의 영향력 행사 가능 sweet spot**
  - 55~60% → 협상 가능
  - 60% 이상 → 사실상 행동주의 무력화

#### 잉여자본비율 = (현금 + 단기투자 - 총부채) / 시가총액
- **임계값**:
  - **> 0.50**: deep cash treasure (강한 후보)
  - **> 0.30**: 행동주의 고려 가능
  - **< 0.10**: 행동주의 대상 부적합

### 4.2 기관 플로우

기관 플로우는 **90D(장기 추세)**와 **20D(단기 모멘텀)** 두 구간을 모두 표시합니다.

#### 매수 VWAP — 기관 매수 단가
- **공식**: 기관이 net 매수한 일자(net volume > 0)의 거래대금 합 ÷ 거래량 합
- **왜 net-buy 일자만**: 매수일과 매도일이 혼합되면 비현실적 VWAP가 산출됨.
  매수 행동 자체의 평균 단가를 분리하기 위해 net 매수일만 사용.
- **리포트 표시**: `매수단가 X원 · vs 현재가 Y%`
  - 양수(+): 기관 평균매수가보다 비쌈 (평가이익, 추격 주의)
  - 음수(-): 기관 평균매수가보다 쌈 (기관 평가손실, 추가매수 or 손절 가능성)
  - 0 근처: 기관 평균매수가 근방 (행동주의 진입 적기)

### 4.3 유동성

#### capacity_score
- `days_to_5pct = (시총 × 5%) / ADV_20D`
- 30일 이하 = 1.0, 90일 이상 = 0.0, 선형 보간

### 4.4 자사주 공시 방향성 (`domain.classify_treasury`)

| 카테고리 | 가중치 | 의미 |
|---|---|---|
| `burn` (소각) | +1.0 | 가장 강한 positive — 유통주식 영구 감소 |
| `buy_done` (취득 결과) | +0.7 | 실집행 매입 |
| `buy_planned` (취득 결정) | +0.4 | 계획만, 미집행 가능성 |
| `trust_open` (신탁 체결) | +0.3 | 매입 의지 표명 |
| `trust_extend` (신탁 연장) | +0.2 | 기존 의지 유지 |
| `trust_cancel` (신탁 해지) | **-0.5** | **매입 계획 취소 = negative** |
| `dispose_planned` (처분 결정) | -0.4 | 자사주 매각 계획 |
| `dispose_done` (처분 결과) | -0.7 | 실집행 매각 |

리포트의 각 공시 라인에는 **DART 원문 링크**(`[공시](URL)`)가 첨부됩니다.
URL은 `rcept_no`를 기반으로 자동 생성: `config.DART_VIEWER_URL` 참조.

### 4.5 5%+ 보고자 분류 (`domain.classify_filer`)

| 분류 | 정의 | 행동주의 시그널 |
|---|---|---|
| `activist` | 명시적 캠페인 이력 (차파트너스, 얼라인, VIP 등) | **강함** |
| `semi_activist` | 가끔 캠페인 (베어링, 한국투자밸류 등) | 중간 |
| `pe_fund` | 사모펀드/투자조합/PEF | 중간 (M&A 가능성) |
| `passive` | 국민연금, KB자산운용 등 | **거의 없음** |
| `strategic` | 그룹 계열사, 모회사 | 통상 없음 (지배강화) |
| `individual` | 개인 (오너 본인 또는 가족) | 분쟁 가능성 별도 분석 |
| `unknown` | 미식별 | 추가 조사 필요 |

**구현**: `domain.PASSIVE = PASSIVE_DOMESTIC | PASSIVE_FOREIGN` 통합 set으로
국내·외 패시브 펀드를 단일 조회로 처리합니다.

**유지보수**: `domain.py`의 `HARDCORE_ACTIVISTS`, `SEMI_ACTIVISTS`, `PASSIVE_DOMESTIC`,
`PASSIVE_FOREIGN`을 분기마다 업데이트.

리포트 표시 기준 날짜는 `config.HOLDINGS_CUTOFF_DATE`(기본값 `"20250101"`)로
중앙에서 관리됩니다. rcept_dt 형식이 `YYYY-MM-DD`와 `YYYYMMDD` 혼재하므로
비교 전 `-` 제거 후 문자열 비교합니다.

### 4.6 섹터 매핑 (KSIC)

`induty_code` 앞 2자리로 섹터 그룹 매핑 (`build_report.induty_to_sector`,
`calibrate._ksic_2digit`).

> **⚠️ 수정 이력 (v3 시절 버그 수정)**: DART `induty_code`는 `"303"(3자리)`, `"2629"(4자리)`,
> `"30399"(5자리)` 등 다양한 길이입니다. `zfill(5)[:2]`를 사용하면
> `"303"→"00303"→"00"` 으로 잘리는 버그가 있었습니다.
> **수정**: `str(code)[:2]` 직접 슬라이싱으로 변경.

### 4.7 LLM 분류 (Gemini 2.5 Pro) — *정성 분류만*

v3에서 LLM의 책임을 축소: **카테고리 + 액션 + 서술**만.
정량 P/Up/Down은 **calibrate.py가 데이터로 산출** (4.8 참조).
LLM이 만드는 P/Up/Down은 sanity-check 보조 표시(`LLM_EV`).

#### 7-카테고리 (mutually exclusive)

1. **승계록인** — 오너 ≥ 60% AND treasury_score ≤ 0
2. **신뢰록인** — 배임/횡령/회계 한정/거래정지 이력
3. **협상력록인** — 단일 거대 고객, 매출↑ OPM 정체
4. **행동주의후보** — recent_activist_filings_12M ≥ 1 AND 오너 40~55% AND 잉여자본 > 0.15
5. **패시브플로우** — 기관 매수 일관 BUT activist filing 없음
6. **정상가치주** — 드묾
7. **기타** — 분류 어려운 경우

### 4.8 ~~Calibration~~ (v4 폐기) → 3축 룰베이스 점수 (v5) — `score_targets.py`

> **v5 메이저 변경 — 왜 calibration을 폐기했나**
>
> v4의 Beta-binomial은 historical 백테스트 (2015~2025, n=205, 12M alpha −3.8%) 를 prior로 사용.
> 그러나 **상법 개정** (이사 충실의무 주주 포함, 일감몰아주기 처벌 강화 등) 으로
> 행동주의 펀드 leverage 환경이 구조적으로 변화. 즉 historical 데이터는
> **regime mismatch** — 구체제 표본을 신체제 예측에 쓸 수 없음.
>
> 데이터 부족도 문제: 신체제 표본은 아직 통계적으로 의미 있는 크기 미달.
> 따라서 calibration source 자체를 신뢰할 수 없음.
>
> **결론**: data-driven prior 폐기, **first-principles 룰베이스 점수**로 전환.
> 가중치는 모두 명시·검토 가능 (`config.py`), 백테스트는 reference only.

### v5 — 3축 점수 시스템

```
TARGET_ATTRACTIVENESS  +  ACCUMULATION_SIGNATURE  +  LEGAL_VULNERABILITY
       ↓                            ↓                           ↓
   잉여자본 多                 90D 매수 일관 +              자사주 처분/해지 +
 + 오너 sweet spot         네트 매수 / 시총 ↑          + 분할/합병 공시 +
 + PBR 갭 큰               + VWAP 안정 +              + 거버넌스 사고 +
 + capacity 충분           + 20D 지속 +                + 임원 churn
                          + 매집 capacity
```

각 축은 **0~100점** 룰베이스 합산. Tier (보고서 우선순위 순):

| Tier | 조건 | 의미 / 행동 |
|---|---|---|
| **HOT** | 3축 모두 ≥ axis_strong (60) | 모든 차원에서 강함, frontrun 1순위 |
| **WARM** | 2축 ≥ axis_strong | 1개 축 약함, 보강되면 HOT 격상 |
| **LATE_SKEPTICAL** | LATE & filing 후 alpha < −5% | **5%+ 공개됐는데 시장 회의적** — 역설적 매수 기회 |
| **WATCH** | 1축 ≥ axis_v_strong (70) | 단일 축 압도적, 다른 축 신호 출현 시 격상 |
| **LATE_ACCESSIBLE** | LATE & alpha −5% ~ +20% | 시장 미온적, 잔여 정보 차익 |
| **LATE_PRICED_IN** | LATE & alpha ≥ +20% | 정보 우위 소진 |
| **LATE** | LATE & alpha 측정 불가 (filing < 7일) | 추가 관찰 |
| **PASS** | 위 어디에도 해당 X | 현재 무신호 |
| **AVOID** | 최대주주 ≥ 60% | 행동주의 무력화 |

#### Tier 우선순위가 LATE_SKEPTICAL을 WATCH 위에 두는 이유

5%+ filing이 공개됐는데도 시장이 KOSPI 대비 −5% 이상 underperform → 정보가 *공개됐음에도* 시장이 회의적 → 정보 우위는 *역설적으로 살아있음*. catalyst 발현 시 큰 폭 상승 가능. 단, 시장이 정당하게 회의적인 경우(진짜 가치함정)도 있으므로 **펀더멘털 강한 LATE_SKEPTICAL** 만 진입 권고.

> **⚠️ Bull market 주의**: KOSPI가 짧은 기간 내 큰 폭 상승하면 사실상 *모든* lagging 종목이 LATE_SKEPTICAL로 분류됨. 이 경우 alpha 음수가 *시장 회의*가 아니라 *섹터 베타* 일 수 있음. 진입 전 섹터별 동향 별도 검증 필요.

### Axis 1 — TARGET_ATTRACTIVENESS (`score_target_attractiveness`)

| 룰 | 가중치 | 데이터 |
|---|---|---|
| 잉여자본비율 ≥ 0.50 (deep_cash) | +30 | screening 출력 |
| 잉여자본비율 0.20 ~ 0.50 (moderate_cash) | +20 | 동일 (둘 중 하나만) |
| 최대주주 40 ~ 55% (sweet spot) | +20 | DART hyslrSttus |
| PBR ≤ sector median × 0.7 | +15 | universe 내 sector median |
| 시총 ≥ 2,000억 (cap_large) | +10 | screening |
| 시총 500 ~ 2,000억 (cap_mid) | +5 | 동일 |
| treasury_score > 0 (자사주 우호) | +15 | enriched.summary |
| **NAV 디스카운트 ≥ 50%** (sum-of-parts) | **+35** | **enriched.subsidiaries (v6)** |
| NAV 디스카운트 30~50% | +20 | 동일 (둘 중 하나만) |

#### Sum-of-parts NAV 계산 (v6 신규)

지주회사·복합기업의 NAV 디스카운트는 한국 행동주의의 **가장 큰 alpha 원천**.
DART `otrCprInvstmntSttus.json` (사업보고서 §VIII 타법인 출자) 데이터로 자동 산출:

```
NAV = Σ(상장 자회사 시총 × 보유 지분) + Σ(비상장 자회사 장부가)
discount_pct = (parent_mcap − NAV) / NAV × 100   # 음수일수록 시총 < NAV (매력)
```

자회사 → 종목코드 매핑은 `corp_code_map.json` 의 corp_name 정규화 매칭 사용 (제거 토큰: 주식회사/(주)/㈜/Co.,Ltd 등).

**한계**:
- 비상장 자회사는 *장부가* 만 사용 (=원시 취득가, 매우 보수적). 실제 가치 underestimate 가능
- 자회사 부채 별도 처리 X — 지주사 자체 부채는 빼지 않음
- 실시간 자회사 시총 fetching (pykrx) → score_targets 실행 시 KRX 콜 추가

### Axis 2 — ACCUMULATION_SIGNATURE (`score_accumulation`)

| 룰 | 가중치 | 데이터 |
|---|---|---|
| 90D 매수일 비율 ≥ 65% | +25 | institutional_flow |
| 90D 누적 net buy / 시총 ≥ 3% | +25 | institutional_flow + 시총 |
| 매수VWAP gap ≤ 5% (가격 통제 매집) | +20 | institutional_flow |
| 20D 매수일 비율 ≥ 50% (지속) | +15 | institutional_flow |
| capacity_score ≥ 0.7 | +15 | liquidity |
| **stake_secrecy** (일평균 매수/ADV ≤ 5%) | **+10** | **flow + liquidity (v6)** |

#### stake_secrecy 룰 — 비밀 매집 가능성

행동주의 펀드는 5% 직전까지 *조용히* 매집해야 함. 일평균 매수액 / ADV가 낮을수록 시장 노출 위험 ↓:

```
daily_buy_avg = (90D net buy 누적) / 60 거래일
secrecy = daily_buy_avg / ADV_20D
secrecy ≤ 0.05  → +10점 (보이지 않게 매집 가능)
```

### Axis 3 — LEGAL_VULNERABILITY (`score_legal_vulnerability`)

| 룰 | 가중치 | 데이터 |
|---|---|---|
| 자사주 처분 결정 12M ≥ 1회 | +20 | enrich treasury_dir_count |
| 자사주 신탁계약 해지 12M ≥ 1회 | +15 | enrich treasury_dir_count |
| 분할/합병 공시 24M ≥ 1건 | +15 | enrich governance_count |
| 거래정지/회계 한정 5Y 이력 | +20 | enrich governance_count.incident_5Y |
| 임원 변동 빈도 비정상 (≥ 8건) | +10 | enrich exec_holdings count |
| **일감몰아주기 proxy (v6 신규)** | **+10~+20** | **subsidiaries 본사명 prefix 매칭 + 비상장 100% 보유** |

#### 일감몰아주기 v6.1 — LLM 정량화 + structural proxy fallback

##### v6.1 LLM 정량화 (`parse_related_party.py`)

DART `/api/document.xml` 으로 사업보고서 본문(ZIP) 다운로드 → 가장 큰 XML 파일에서
"특수관계자와의 거래" 섹션 ~12,000자 추출 → **Gemini 2.5 Pro** structured output 으로:

- `related_party_sales_won`: 특수관계자에게 판매한 매출 합계
- `total_revenue_won`: 회사 총매출 (사업보고서 §X 에는 보통 누락 → yfinance 매출_4Y 첫 값으로 후처리)
- `ratio_pct`: 매출 비중 (%)
- `confidence`: high / medium / low
- `evidence`: 텍스트 인용

**점수 룰** (`score_legal_vulnerability`):

| ratio_pct | confidence | LEGAL 가점 |
|---|---|---|
| ≥ 10% | high/medium | +20 (related_party_quantified) |
| 5~10% | high/medium | +10 (related_party_partial) |
| 그 외 | — | structural proxy fallback |

##### Structural proxy fallback

LLM 분석 없거나 low confidence 시:
- 자회사 중 본사명 prefix(2자) 매칭 ≥ 3 AND 100% 보유 비상장 자회사 ≥ 5 → **+20** (review flag)
- 또는 본사명 prefix 매칭 ≥ 5 → **+10**

##### 비용 / 운영
- universe ~50종목 × Gemini 1 call ≈ 5분 / 무료 티어 충분
- 분기 1회 사업보고서 갱신 시점에 재실행 권장
- enrich_dart.py `--report-text` 플래그로 텍스트 추출 단계 조절 가능

##### 실제 결과 예시 (universe 48개)

| 종목 | ratio_pct | confidence | tier 결과 |
|---|---|---|---|
| 사조오양 | **35.2%** | medium | AVOID (오너 76%, 일괄 차단) |
| 한국공항 | **77.7%** | medium | PASS (대한항공 captive, 매력 X) |
| 평화홀딩스 | 6.5% | medium | PASS |
| 그 외 (39개) | < 5% | low/medium | 통상 |

→ 데이터로 **사조계열 / 한국공항** 의 일감몰아주기 패턴이 명확히 드러남. 사조계열은 오너 60%+ 차단, 한국공항은 대한항공 100% 자회사라 본질적으로 행동주의 타깃 X.

### 정보 우위는 어디서 발생하는가 — "HOT은 선반영 아닌가?"

타당한 질문. v5 시스템의 정보 우위를 축별로 분해하면:

| 축 | 시장 반영도 (추정) | 이유 |
|---|---|---|
| TARGET (잉여자본·PBR 등) | **50~70% 반영됨** | 펀더멘털은 누구나 봄, PBR 디스카운트로 일부 가격에 반영 |
| ACCUMULATION (90D 매수일·VWAP 안정) | **10~30% 반영** | 일별 매수 데이터는 공개되지만 *체계적으로 감시*하는 사람 적음 |
| LEGAL (자사주 처분·분할·사고) | **30~50% 반영** | 큰 사건은 알려졌으나 *누적 의미*는 시장이 미파악 |

→ 정보 우위는 *단일 축이 아니라 **3축 동시 발현** 감지*에서 옴. 시장은 PBR 낮은 건 보지만, "이 종목은 매집 + 법적 leverage 까지 갖췄다"는 *조합* 은 안 봄.

#### 자동 over-pricing 보호 — `vwap_near` 룰

ACCUM 축의 `vwap_near` 룰: 현재가 vs 매수VWAP gap **≤ 5%** 일 때만 +20점. **이미 너무 오른 종목은 ACCUM 점수가 자연 감점**:

| 시나리오 | vwap_near | ACCUM 결과 |
|---|---|---|
| 매집 흔적 강 + 가격 +30% 상승 | 미충족 (gap > 5%) | ACCUM 점수 ↓ → HOT 진입 어려움 |
| 매집 흔적 강 + 가격 안정 | 충족 (gap ≤ 5%) | ACCUM ≥ 60 가능 → HOT/WARM |

이게 v5의 핵심 design 포인트. **선반영된 종목은 자동으로 HOT에서 제외**됨.

또한 LATE 트랙은 5%+ 공개 후 alpha를 명시적으로 측정해 PRICED_IN/ACCESSIBLE/SKEPTICAL 분리 — 이중 안전장치.

### 4.8.2 Sanity check (v6 신규) — yfinance 데이터 검증

`fundamentals_sanity_flags` 가 의심값을 자동 감지·flag:

```
잉여자본비율 > 5.0   → 시총의 5배 초과 순현금 = yfinance 잘못 계산 가능성
잉여자본비율 < −3.0  → 극단적 순부채
PBR < 0.05 또는 > 5  → 비정상 범위
```

플래그된 종목은 해당 룰이 score 산출에서 *제외*되며, 리포트 카드에 ⚠️ 표시.
예: 다우기술 잉여자본비율 15.371 → flag → deep_cash 룰 비활성화.

### 4.8.4 AVOID 룰 정교화 (v6.1) — 60~65% 면제 조건

상법 개정으로 60%+ 오너지분도 *부분적으로* 공격 가능 (주주대표소송 leverage).
v6.1에서 AVOID 임계값 60→65 완화 + 60~65% 구간 면제 조건:

```
오너 ≥ 65% → AVOID 일괄

오너 ∈ [60, 65) AND (
    NAV 디스카운트 ≤ −30%      → 면제 (지주사 deep value)
    OR treasury_score ≥ +1.0   → 면제 (자사주 우호 정책)
)
→ AVOID 면제, 다른 tier 분류

오너 ∈ [60, 65) 면제 조건 미충족 → AVOID
```

각 종목의 `avoid_reason` 필드에 차단/면제 사유 기록.

**실제 결과 예시**:
- 롯데렌탈 (오너 61.21%, treasury_score +1.1) → 면제 → LATE_SKEPTICAL
- 사조대림 (오너 64.65%, NAV 디스카운트 −49%) → 면제 → PASS
- 사조오양 (오너 76.31%) → 일괄 AVOID

### 4.8.5 Captive subsidiary 자동 식별 (v7 P2)

행동주의 입장에서 *captive 자회사* (모회사 의무거래로 매출 대부분 단일 모회사 의존)는
해체 동기 X → 진입 의미 없음. 자동 차단:

```
captive 조건 (둘 다 만족):
  ① 일감몰아주기 비중 ≥ 50% (CAPTIVE_RELATED_PARTY_PCT)
  ② 최대주주가 strategic 법인 AND 지분 ≥ 50% (또는 owner ≥ 50%)
→ AVOID with reason "CAPTIVE: 의무거래로 행동주의 무력"
```

실제 사례:
- 한국공항: 일감 77.7% + 대한항공 모회사 → AVOID

### 4.8.6 Catalyst Timing (v7 P1) — 사외이사 임기 만료

DART `exctvSttus.json` 의 `tenure_end_on` 필드 (예: "2027년 03월 26일") 파싱하여
사외이사 임기 만료 D-Day 추적.

```
LEGAL_WEIGHTS["outside_director_expiry"] = 15
조건: 사외이사 임기 만료 ≤ 180일 (CATALYST_TIMING_DAYS)
→ 정관 변경 / 사외이사 추천 leverage 발휘 시점
```

실제 사례:
- 세원정공: 사외이사 이병찬 D-143 → +15 LEGAL 점수 트리거 (총 LEGAL 20→35)

### 4.8.7 NAV 신뢰도 점수 (v7 P3)

NAV가 *비상장 자회사 위주* 일 경우 장부가 underestimate → 시총 premium 시그널이
false positive 가능. trust score 자동 부여:

```
listed_share_pct = listed_nav / total_nav × 100
HIGH   → ≥ 50%   (시총 premium / 디스카운트 모두 trust)
MEDIUM → 25~50%
LOW    → < 25%   (premium 무시, 디스카운트만 trust)
```

→ 한국공항 (+1018% premium, trust LOW) 의 false signal 자동 억제.

### 4.8.8 Causal validation framework (v7 P4) — `validate_signals.py`

상법 개정 효과를 시간 분리 측정:
- pre-amendment (filing < 2024-07-01)
- post-amendment (filing ≥ 2024-07-01)

backtest_activist 의 12M alpha 비교. **현재 결과** (n_pre=183, n_post=11):
- pre  α12M 평균: −3.4%
- post α12M 평균: −10.3%
- Δ (post − pre): −6.9%

→ **regime change effect 미확인** (표본 부족 + KOSPI bull market 영향).
이는 v5에서 historical calibration 폐기한 결정의 정당성을 강화. 룰베이스 first-principles
가 데이터로 *지지되지는 않지만 부정도 안 됨* — 환경이 안정화되면 재측정.

### 4.8.9 Real-time DART filing alert (`monitor.py`)

**일별 cron 운영용**:
```bash
0 9 * * * cd /path/to/screening && python monitor.py
```

작동:
1. DART list.json 으로 어제 ~ 오늘 분 5%+ 보고 (pblntf_ty=D) 전체 수집
2. scores.json watchlist (AVOID 제외) 매칭
3. classify_filer 로 행동주의/PE 시그널만 필터
4. 매칭 시 console 출력 (DART URL 포함)

**가치**: 5%+ 공시 → 시장 반영까지 보통 60~90분. 그 사이 수동 모니터링 부담 X.

### 가중치 튜닝 정책

- 모두 `config.py` 의 `TARGET_WEIGHTS` / `ACCUM_WEIGHTS` / `LEGAL_WEIGHTS` 에서 관리
- 데이터 핏팅 X (overfit 방지) — 도메인 지식과 운영 경험 기반
- 분기 1회 review: 신규 캠페인 사례 vs 점수 정합성 검토 후 조정
- 변경 시 git diff 로 의사결정 추적 가능

### 4.8.1 LATE sub-tier — filing 후 KOSPI alpha 기반 분리

5%+ filing이 *공개*됐다고 해서 정보 우위가 *완전히 소진*된 건 아님. 시장 반응에 따라
정보 차익이 잔존할 수 있음. v5.1에서 LATE를 4 sub-tier로 분리:

```
filing 후 alpha = (stock_return) − (KOSPI 종합지수 동기간 return)
```

| Sub-tier | 조건 | 해석 | 진입 우선순위 |
|---|---|---|---|
| **LATE_PRICED_IN** | alpha ≥ +20% | 시장이 이미 반영 | 진입 의미 X |
| **LATE_ACCESSIBLE** | −5% ≤ alpha < +20% | 시장 미온적 | 잔여 차익 가능, 매수 검토 |
| **LATE_SKEPTICAL** | alpha < −5% | 시장 회의적 | **WATCH 위 우선** (단, sector beta 점검 필수) |
| **LATE** | alpha 측정 불가 (filing 후 < 7일) | 추가 관찰 | 별도 |

#### 구현 (`score_targets.post_filing_alpha`)

1. enriched.summary.recent_activists 에서 가장 최근 filing date 추출
2. 그 일자부터 오늘까지 stock OHLCV (pykrx) 수집
3. 같은 거래일 범위의 KOSPI 종합지수 (1001) slicing
4. 종목 종가 변화율 − KOSPI 종가 변화율 = alpha (%)

**캐싱**: KOSPI 인덱스는 충분히 긴 lookback(800일)으로 한 번만 fetch, 종목별로 slicing.

#### 임계값 근거

- `+20%` priced_in: 일반 활동주의 캠페인 1차 반영 폭 (10~25% 범위 가운데)
- `−5%` skeptical: 단순 noise(±3%) 를 넘어선 의도적 회피의 통계적 마진
- 임계값은 `config.LATE_THRESHOLDS` 에서 조정 가능

#### 한계

- **Bull market dilution**: KOSPI가 큰 폭 상승하면 lagging 종목이 자동 LATE_SKEPTICAL로 분류됨. 시장 회의가 아니라 *섹터 베타* 일 수 있으므로 사용자가 별도 판단 필요.
- **단일 통제군 (KOSPI)**: 섹터 / 사이즈 / 스타일 베타는 미공제. v6 에서 multi-factor 통제 검토.
- **단일 filing 만 사용**: 같은 종목에 여러 filing이 있을 때 가장 최근 만 사용. 누적 매집 패턴은 미반영.

### Historical 백테스트 (참고용)

`backtest_activist.json` 은 *과거* 5%+ filing 의 KOSPI alpha 분포를 보여줍니다.
v5에서는 **calibration prior 로 사용하지 않고** 다음 용도로만:

- 회의용 reference: "이전 regime에서는 alpha 평균 −3.8%였다"
- 분기 review 입력: 새 캠페인 사례 추가 시 historical 분포와 비교
- 새 데이터 30~50건 누적되면 v6 에서 재도입 검토

### ~~v4 Beta-binomial 잔재 (참고)~~

(v4 시절 stratified posterior P 결과는 git history 또는 이전 버전 참조)

### 4.9 AGM 타임라인

| Phase | 기간 | 행동주의 PM 행동 |
|---|---|---|
| **stake_building** | 주주제안 마감 60일 전 이전 | 조용히 매집, 5%+ 임박 시 신고 |
| **campaign_window** | 마감 60일~당일 | 공개 서한, 주주제안 제출 |
| **agm_window** | 마감 후 ~ 주총일 | 위임장 경쟁, 미디어 캠페인 |
| **post_agm** | 주총 직후 ~ 다음 stake_building | 결과 통합, 다음 사이클 준비 |

한국 KOSPI: 정기주총 3월 31일경, 주주제안 마감 6주 전(≈ 2월 중순).

---

## 5. 백테스트 — KOSPI alpha 기반

### 5.1 목적

> "행동주의 5%+ filing이 *KOSPI 인덱스 대비 초과수익(alpha)*을 만드는가?"

### 5.2 방법

1. enriched.json의 모든 5%+ filing에서 `filer_type ∈ {activist, semi_activist, pe_fund}` 추출
2. KOSPI 종합지수(1001) OHLCV prefetch
3. 각 filing 일자 → +6M/+12M:
   - `alpha = stock_return − kospi_return`
4. 승률 = alpha > 0 비율 → calibrate.py 입력

### 5.3 핵심 백테스트 인사이트 (과거 10년 기준)

행동주의 공시(PBR≤0.8)에 대한 심층 백테스트 결과, 성공(12M 절대/상대 수익률 창출)을 좌우하는 3대 요소가 식별되었습니다.

1. **PBR 마지노선 (0.6)**:
   - **PBR < 0.4**: 승률과 중앙값이 가장 높은 '안전 마진' 구간 (하방 경직성 최고).
   - **0.4 ≤ PBR < 0.6**: 평균 수익률이 극대화되는 '홈런' 구간.
   - **0.6 ≤ PBR ≤ 0.8**: 승률 50% 미만으로 급감하며 중앙값이 마이너스로 전환되는 '위험' 구간. 가급적 타깃 PBR은 0.6 미만으로 한정.

2. **펀드 주체 (Who)**:
   - **안정+고수익**: 한국투자밸류자산운용, 신영자산운용 (승률 60~70%대, 높은 중앙값).
   - **High Risk / High Return**: VIP자산운용 (승률은 낮으나 평균 수익률 견인).
   - 행동주의 펀드라고 해서 무조건 맹신할 수 없으며, 레코드가 입증된 펀드의 '경영참가' 목적 공시만 신뢰할 것.

3. **극단적 계절성 (Seasonality)**:
   - 한국의 5% 대량보유공시(경영참가 목적)는 **100% 1분기(1~3월)**에 발생.
   - 3월 말 정기주주총회를 겨냥해 늦어도 2월 중순(주주제안 마감일) 이전에 모든 액션이 집중됨.
   - **전략**: 주총 시즌(Q1)에 행동주의 타깃을 매수하여 주총 전후의 변동성 또는 12개월 뒤 리레이팅을 노리는 것이 최적. 비시즌(Q2~Q4)에는 발생 빈도가 극히 낮음.

### 5.4 한계 (정직 공시)

- **Reverse survivorship**: universe가 *현재 PBR ≤ 0.8 유지* 종목 → 성공해서 PBR 올라간 종목 제외 편향
- **KOSPI 인덱스만 통제**: sector/factor beta 미공제
- **배당 미반영**: pykrx 종가만. 가치주 배당이 30%+ 비중일 수 있음

### 5.4 운영 권장

- 백테스트는 *방향성 sniff test*로 사용.
- EV_model 음수라도 진입 자체를 막지 말 것 — base rate가 학습 부족 때문일 수 있음.
- Kelly 분수: `f* = EV / (Up × Down)` → 음수면 long 진입 안 함. 양수여도 1/4 Kelly 권장.

---

## 6. 코드 구조 / 상수 관리

### 6.1 단일 진실 원칙

| 관심사 | 위치 |
|---|---|
| API 키, 파일 경로 | `config.py` |
| 컷오프 날짜(`HOLDINGS_CUTOFF_DATE`) | `config.py` |
| DART 뷰어 URL 템플릿(`DART_VIEWER_URL`) | `config.py` |
| 행동주의 펀드 화이트리스트 | `domain.py` |
| 자사주 방향성 룰 | `domain.py` |
| KSIC 섹터 그룹 맵 | `build_report.py` `INDUTY_GROUPS` |

> `HOLDINGS_CUTOFF_DATE = "20250101"` — 5%+ 보유공시 리포트 필터 기준.
> 연초 갱신 시 이 상수만 변경하면 `enrich_dart.py`, `build_report.py` 모두 반영됩니다.

### 6.2 섹터 매핑 주의사항

DART `induty_code` 길이는 3~5자리로 혼재합니다.
`zfill(5)[:2]` 방식을 사용하지 마세요. `str(code)[:2]` 직접 슬라이싱을 사용합니다.

### 6.3 DART 공시 URL

리포트의 자사주 공시와 5%+ 보유공시에는 DART 원문 링크가 자동 첨부됩니다.

```
https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}
```

- 자사주 공시(`treasury_disclosures`): `list.json` API 응답에 `rcept_no` 포함 → 즉시 사용 가능
- 5%+ 보유공시(`major_holdings_5pct`): `majorstock.json` API에서 `rcept_no` 수집
  (v3에서 `enrich_dart.fetch_major_holdings`의 `keep` 필드에 추가)

---

## 7. 알려진 한계 및 향후 작업

### 7.1 데이터 누락
- yfinance 한국 종목 분기/연 재무 일부 누락 → 후보 자동 탈락
  - 보완: DART 사업보고서 직접 파싱

### 7.2 행동주의 화이트리스트
- 한국 행동주의 펀드는 자주 신생/재편 → **분기 업데이트 필수**
- `domain.PASSIVE` 통합 set에 신규 패시브 펀드 추가 시
  `PASSIVE_DOMESTIC` 또는 `PASSIVE_FOREIGN` 중 적절한 쪽에 추가하면 됩니다.

### 7.3 LLM 분류 신뢰도
- 환각 가능성: system prompt에서 금지하나 100% 차단 불가
- 모델 변경 시 reproducibility 깨짐 → `GEMINI_MODEL` 고정 + temperature 0.2

### 7.4 거래 비용
- ADV 기준 capacity는 단순 매집 가능성. 실제 슬리피지/시장임팩트 미반영

---

## 8. 운영 체크리스트

### 분기 정기 운영

- [ ] `build_corp_code.py` 재실행 (DART 신규 상장사 반영)
- [ ] `domain.py` 행동주의 화이트리스트 업데이트
- [ ] `config.py` `HOLDINGS_CUTOFF_DATE` 연초 갱신 (예: `"20260101"`)
- [ ] `screen_value_ownership.py` `--year` 인자를 최신 사업보고서 연도로 변경
- [ ] 백테스트 모집단 확장 검토
- [ ] `python -m activist_scout.pipeline --from enrich` 로 공시 데이터 갱신 (5%+ URL 포함)

### 진입 전 due diligence

- [ ] 사업보고서 본문 (DART) §I.사업의 내용, §VII.주주에 관한 사항 직접 확인
- [ ] 최근 6개월 정정공시, 횡령/배임 관련 형사 공시 검색
- [ ] 회계감사인 변경 여부 (지난 5년)
- [ ] 자회사/특수관계인 거래 (사업보고서 §X)
- [ ] AGM 타임라인 vs 진입 시점 정합성
- [ ] 기관 매수 단가 (90D/20D VWAP) vs 현재가 괴리 확인

---

## 9. 참고

- 자본시장법 §147~149 (주식 등의 대량보유 등의 보고)
- KIND 「밸류업 가이드라인」 (2024)
- DART OpenAPI: <https://opendart.fss.or.kr/guide/main.do>
- pykrx 문서: <https://github.com/sharebook-kr/pykrx>

*Last updated: 2026-05-11 (v0.1.0 public 기준) — v7 파이프라인: screening 8 stage
(screen → fetch_flow → fetch_liq → enrich → parse_rp → classify → score → report),
related-party LLM 정량화, captive subsidiary 자동 식별, NAV trust score,
사외이사 임기 만료 catalyst, causal validation framework.*
