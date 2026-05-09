# 예시 출력

본 디렉토리는 `activist-scout` 실행 결과의 *공개 가능한* 예시를 포함합니다.

## 디렉토리

```
examples/
├── README.md              # 이 파일
└── sample_reports/        # IC급 deep dive 보고서 예시
    ├── deep_dive_021820.md   # 세원정공 (자동차부품 + 거버넌스 사고 케이스)
    ├── deep_dive_016740.md   # 두올 (PE 14.65% 진입 케이스)
    └── ...
```

## 예시의 목적

1. **시스템 결과 형식 미리보기** — 직접 실행 전 출력물 확인
2. **케이스 학습** — 실제 KOSPI 종목들의 분석 패턴
3. **자동화 한계 인지** — §13 "사람 검증 필수" 섹션이 어떻게 작성되는지

## 직접 생성

```bash
# 예시와 같은 보고서 직접 생성
python -m activist_scout.deep_dive 021820 \
    --output reports/sewon.md
```

## ⚠️ 주의

- 본 예시들은 **2026-05-09 시점** 데이터 기반.
- KOSPI 시장 상황·5%+ filing·재무 데이터는 빠르게 변합니다.
- **현재 시점 의사결정에 직접 사용하지 마세요.**
- [DISCLAIMER.md](../DISCLAIMER.md) 참조.
