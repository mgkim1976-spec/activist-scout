# IC Memo — KOSPI 행동주의 후보 6종 진입 결정

*작성: 2026-05-09 · PM*
*기반: deep_dive.py v5 6 종목 보고서 (`reports/deep_dive_*.md`)*

---

## TL;DR

| 결정 | 종목 | 사이즈 | 핵심 trigger |
|---|---|---|---|
| **CONSIDER (Tier A)** | **016740 두올** | **1.0%** | PE 14.65% 진입 D-5, 순 EV **+11.7%** |
| **PILOT (0.5% 시작)** | **021820 세원정공** | **0.5%** | 사외이사 D-141, deep cash 1.9× |
| **PILOT (0.5%)** | **023350 한국종합기술** | **0.5%** | T 70 + A 60, 순점수 65 (WATCH 상단) |
| WATCH | 020120 키다리스튜디오 | 0% | catalyst 부재 (12M 큰 공시 0건) |
| WATCH | 005720 넥센 | 0% | OEM 종속 + 순부채, VIP 매집 진행만 |
| **AVOID** | 089860 롯데렌탈 | 0% | 순점수 **-58** (FCF -46%, captive) |

**총 진입**: 2.0% (10+5+5 억, 1,000억 portfolio 기준 20억)
**현금 유지**: 98%
**Risk budget 합계**: -25% × 2.0% = **-0.5% portfolio PnL impact**

---

## 1. 6 종목 정량 비교

| 티커 | 종목 | tier | 순점수 | 순 EV | PBR | 잉여자본 | ROE | FCF yield | filing α | 결정 |
|---|---|---|---|---|---|---|---|---|---|---|
| **016740** | **두올** | LATE | **80** | **+11.7%** | 0.51 | 0.02 | — | — | — | **🟢 CONSIDER 1.0%** |
| **023350** | **한국종합기술** | WARM | **65** | — | 0.33 | 0.98 | 5.0% | 34.3% | — | **🟢 PILOT 0.5%** |
| 005720 | 넥센 | LATE_SKEPTICAL | 53 | +9.7% | 0.26 | -3.30 | 6.5% | 34.1% | -136.9% | ⚪ WATCH |
| **021820** | **세원정공** | WATCH | 45 | +8.4% | 0.24 | 1.90 | 6.8% | 26.3% | — | **🟢 PILOT 0.5%** |
| 020120 | 키다리스튜디오 | WARM | 39 | +8.0% | 0.72 | 0.17 | 3.3% | 8.3% | — | ⚪ WATCH |
| 089860 | 롯데렌탈 | LATE_SKEPTICAL | **-58** | — | 0.74 | -3.67 | 8.1% | **-46.2%** | -35.7% | 🔴 **AVOID** |

---

## 2. 종목별 PM Thesis (각 보고서 §0 추출)

### 🟢 016740 두올 — 1순위
> **Technically 저PBR·고FCF의 행동주의 textbook setup but 자동차시트 OEM 종속 + M&A 진행 중 지배구조 재편으로 기존 최대주주 leverage가 왜곡 → unique trigger path is PE 펀드 14.65% 진입 후 주주환원/거버넌스 재편**

- **D-5 PE filing**: 프리미어성장전략엠앤에이사모투자합자회사 14.65% (2026-05-04)
- **동시 M&A**: 모트렉스이에프엠 62.23% (지배구조 재편 임박)
- **순 EV +11.7%**: catalyst probability matrix 가장 높음
- **catalyst window**: 2027-03-28 정기주총 (D-322), 그러나 M&A 캠페인은 더 빠름

### 🟢 021820 세원정공 — 2순위
> **Technically textbook deep value but 4,200억 배임·3년 거래정지 전력으로 신뢰 회복이 안 됨 → unique trigger path is 외부 5%+ 매도 지속 + 주총/사외이사 임기 만료 시점의 자본환원 압박**

- **잉여자본 1.9× (시총의 1.9배 순현금)** — 한국 KOSPI 상위권
- **6월 결산 catalyst** D-141 (2026-09-28 사외이사 동시 만료)
- **거래정지 400일** historical, 시장 재신뢰 미완성
- 외국계 fund 부재 (외국인 60D -10.4억)

### 🟢 023350 한국종합기술 — 3순위
> **Technically deep-cash but 산업 모멘텀·대주주 지배력·captive 매출 구조가 상시 디스카운트 요인 → unique trigger path is 현물배당 이후 주주환원 확대 + 외부 5%+ 캡핑 실패**

- **T 70 + A 60 = 순점수 65** (WATCH 상단, CONSIDER 직전)
- ROE 5.0% < CoE 10% → -5%p (자본 비효율)
- FCF yield 34.3% (강한 환원 여력)
- 자사주 신탁 연장만 반복 → 정책 진정성 약함

### ⚪ 005720 넥센 — WATCH
> Technically textbook value+activism setup but **OEM 종속·고부채·자기주식 처분 반복**으로 leverage 약화

- VIP자산운용 매집 진행 (2025-03~07)
- 순부채 -3.30 (현금 < 부채)
- filing α -136.9% (KOSPI rally underperform — sector beta + idiosyncratic)
- *진입 전 부채 구조 직접 검증 필요*

### ⚪ 020120 키다리스튜디오 — WATCH
- 12M 큰 공시 부재 = catalyst 없음
- T 60 + A 60 (매집 시그널 강) 그러나 *L 20* (legal leverage 약)
- 0.5% 선적립 → 자사주 소각/외국계 5%+ 등 trigger 출현 시 1.5%까지

### 🔴 089860 롯데렌탈 — AVOID
> **순점수 -58, FCF yield -46.22%, net debt, group captive**

- **FCF 마이너스 46%**: 시총의 절반에 해당하는 cash drain
- 롯데지주 captive subsidiary
- VIP 6.20% 진입했지만 leverage 약함 (PBR 0.74 > sector median)
- *형식적 LATE_SKEPTICAL 일 뿐, 진짜 우월 시그널 X*

---

## 3. 진입 결정 정량 근거

### Risk-adjusted sizing (1,000억 portfolio 가정)

```
Tier A (두올 1.0%):       position 10억, downside 25% → -0.25%
Tier B (세원정공 0.5%):    position 5억,  downside 25% → -0.13%
Tier B (한국종합기술 0.5%): position 5억,  downside 25% → -0.13%
─────────────────────────────────────────────────────────────
Total deployed:           20억 (2.0%)
Total risk budget:        -0.51% portfolio PnL impact
Cash remaining:           980억 (98%)
```

### Catalyst window (최우선 순)

| 일자 | 종목 | 이벤트 |
|---|---|---|
| 2026-05-08 (오늘) | **016740 두올** | PE 14.65% filing D-5 → 즉시 진입 결정 |
| **2026-08-17** | 021820 세원정공 | **주주제안 마감 (D-99)** ← 가장 가까운 catalyst window |
| 2026-09-28 | 021820 세원정공 | 정기주총 + 사외이사 만료 동시 |
| 2027-03-22~31 | 005720, 020120, 089860 | 정기주총 (12월 결산) |

### EV (Catalyst Probability × Impact)

```
두올      +11.7% > 한국종합기술 ~+10% > 넥센 +9.7% > 세원정공 +8.4% > 키다리 +8.0%
```

→ **순 EV 기반으로도 두올이 최우선**, 세원정공은 안전마진 + catalyst 시점 우위.

---

## 4. 실행 plan

### Day-by-Day (이번 주 ~ 다음 주)

| Day | 작업 |
|---|---|
| **Day 1 (오늘)** | 두올 1.0% 매입 시작 (시장가 분할). 매수 VWAP gap +6.1% — *추격 매수 주의*, 약세 시 진입. *우선*: PE filing 본문 download → 자금조성·보유목적 확인 |
| Day 2 | 세원정공 0.5% pilot 매입. 평균매수가 14,500~16,000원 목표 |
| Day 3 | 한국종합기술 0.5% pilot 매입 |
| Day 4~5 | 두올 매수 보강 (1.0% 채울 때까지) + 세원정공 4,200억 배임 외부 검증 |
| Day 6 | monitor.py 일별 cron 활성화 → 새 5%+ filing 즉시 alert |
| Day 7 | IC 정기 review (다음 주 월요일) |

### Exit conditions (진입 후)

#### 두올 (BUY)
- ✅ Trigger 발현: PE 추가 매수, 자사주 소각 결의, 신임 사외이사 추천 → 1.5% 증액
- ❌ Stop: PE 보유목적이 단순투자 또는 청산 / M&A 무산 → 0.5%로 축소

#### 세원정공 (PILOT)
- ✅ Trigger 발현: 외국계 fund 5%+ 진입, 자사주 소각 결의 → 1.5% 증액
- ❌ Stop: 신규 횡령·배임 공시, 사외이사 재선임 무난 통과 → 청산

#### 한국종합기술 (PILOT)
- ✅ Trigger 발현: 외부 5%+ 신규 보고, 신탁계약 연장 X → 1.5%
- ❌ Stop: 정부 발주 의존 patterns 발견 → 청산

---

## 5. Risk factors (전체)

### 시스템적 리스크
1. **bull market dilution**: KOSPI rally 시 모든 lagging 종목이 LATE_SKEPTICAL — 진짜 시그널 약화
2. **regime change**: 상법 개정 효과가 historical alpha와 다름 (post-amendment 표본 부족)
3. **liquidity stress**: 1.5% 진입 시 매집 200~250일 소요 (소형주 limit)

### 종목별 specific
1. **두올**: M&A 무산 시 -20% 가능 (지배구조 변경 unwind)
2. **세원정공**: 신규 배임 공시 발생 시 거래정지 가능 (-30~50%)
3. **한국종합기술**: 정부 발주 cap (captive 우려) 검증 필수

### 사람 영역 (자동화 X)
- 외국계 fund 한국 입국 신호 — Bloomberg/헤드헌터/미디어
- 두올 PE 자금조성 의도 (캠페인 vs 단순 M&A)
- 세원정공 4,200억 사건의 *현재* 의미 (검찰 후속)
- 매니지먼트 인터뷰 시도

---

## 6. Monitoring

```bash
# 일별 cron — 새 5%+ filing 즉시 alert
0 9 * * * cd /Users/mg_mac/MGPrj/screening && python monitor.py

# 주간 review — 매주 월요일
python pipeline.py                   # universe 재계산
python deep_dive.py <new ticker>     # 새 actionable 종목 deep dive

# 분기 backtest 갱신
python pipeline.py --pipeline backtest
python validate_signals.py
```

---

## 7. IC 결정 요약

| | 결정 | 근거 |
|---|---|---|
| **승인** | 두올 1.0% (10억) | 순 EV +11.7%, fresh PE filing, M&A trigger |
| **승인** | 세원정공 0.5% (5억) pilot | deep cash + 사외이사 catalyst, 4,200억 reservation |
| **승인** | 한국종합기술 0.5% (5억) pilot | 순점수 65, 자본 비효율 정량 |
| **부결** | 롯데렌탈 (AVOID) | FCF -46%, captive |
| 보류 | 키다리, 넥센 (WATCH) | catalyst 부재 또는 부채 우려 |

**Total deployed**: 20억 (2.0%)
**Risk budget consumed**: -0.51% portfolio PnL impact
**Maximum sizing trigger**: 각 종목 catalyst 발현 시 1.5%까지 증액 (총 4.5% deployed 가능)

**다음 IC**: 2026-05-16 (월) — 1주 monitoring 후 trigger 확인 + 사이즈 조정

---

## 부록 — 보고서 위치

```
reports/
├── IC_MEMO.md                  ← 본 메모
├── deep_dive_016740.md         두올      (22 KB)
├── deep_dive_021820.md         세원정공  (23 KB)
├── deep_dive_023350.md         한국종합기술 (24 KB)
├── deep_dive_020120.md         키다리스튜디오 (25 KB)
├── deep_dive_005720.md         넥센      (28 KB)
└── deep_dive_089860.md         롯데렌탈   (23 KB)
```

각 보고서는 v5 13-section IC-grade format (PM Thesis / 펀더멘털 / 수익성 / NAV / 자회사 / Peer / 잉여자본 / 최대주주 / 외국인 / 거버넌스 / 임원보수 / 자사주 history / Catalyst Timing / 5Y 주가 / 점수 매트릭스 / Catalyst EV matrix / 운영 plan / 결론 / 사람영역 분리).

