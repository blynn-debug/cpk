# 마켓 레이더 설계 (니치 발굴 · 꾸준함 검증)

작성 2026-09-21 · 상태: 설계 합의됨(구현 전) · 대상 프로젝트: cpk

## 1. 목적 (CORE 문장)

위탁판매 셀러가 **"레어하지만 매니아 수요가 꾸준하고, 팔리는(반품 적은) 시장"을 못 찾는 상태**와,
**그런 후보 시장을 점수화한 shortlist로 받고 '꾸준함'을 시계열로 검증한 상태** 사이의 차이를 줄인다.

- 현재: cpk는 온디맨드 1건 검색 = "지금 스냅샷"만 준다 → **변화(꾸준함)를 못 본다.**
- 기대: 후보 키워드를 자동 수집·점수화하고, 며칠~몇 주 추이로 "꾸준한 니치"를 가려낸다.
- 판정 기준: 대시보드가 경쟁·수요·꾸준함을 점수로 보여주고, **소싱 가능성은 도매꾹 존재판별로**, **반품 위험은 사람이** 최종 판단.

## 2. 범위 / 비범위

**범위(MVP)**
- 후보 키워드 발굴(seed → 연관·자동완성 확장).
- 후보 키워드 SERP 매일 자동 수집(시계열 스냅샷).
- 기회 점수화(경쟁·수요 즉시 + 꾸준함 누적).
- **도매꾹 존재판별 게이트**(국내배송 & 우수공급사) — 소싱 가능 후보만 부각.
- 읽기 전용 대시보드(shortlist + 키워드별 추이).

**비범위(나중)**
- 다중 사용자·인증, 알림, 상품단위 추적, 리뷰텍스트 반품신호, 자동 승격, 도매꾹 마진·발주 연동.

## 3. 아키텍처 (맥미니 중심 · 얇은 클라우드)

```
seed → (연관·자동완성 확장) → 후보 키워드
        │  매일 launchd
        ▼
   맥미니: cb.search(프록시) → 스냅샷 → SQLite(market.db)
        │            └ 도매꾹 오픈API 존재판별(sourcing)
        ▼  점수 계산
   cpk_market_report.py (JSON)
        │  기존 SSH 다리(:443, 제한키)
        ▼
   Railway 대시보드(/markets, 읽기 전용)
```

- **크롤은 검증된 맥미니 경로 재사용**(진짜 Chrome + KR 주거 프록시 + 요청제어). Railway는 화면·중계만.
- 도매꾹은 **공식 오픈API**(HTTPS, 키 인증)라 맥미니에서 직접 호출(프록시 불필요).

## 4. 데이터 모델 (SQLite: `~/cpk/state/market.db`)

- `keywords(id, keyword, source[seed|related|auto], status[candidate|tracked|archived], parent_seed, added_at)`
- `snapshots(id, keyword_id, day, result_count, units_shown, rocket_cnt, seller_rocket_cnt, general_cnt, ad_cnt, review_sum, review_max, price_min, price_med, price_max, top_json, ts)` — 키워드·날짜당 1행.
- `sourcing(keyword_id, checked_at, dome_count, dome_exists, supply_count, supply_exists)` — 도매꾹 존재판별 결과 캐시.
- `scores(keyword_id, day, rarity, demand, steadiness, opportunity)` — 스냅샷 기반 산출(재계산 가능).

## 5. 발굴 (seed → 확장)

- 사용자가 seed 키워드 목록 제공(`state/seeds.txt` 또는 대시보드 입력).
- 확장: 각 seed의 **연관검색어 + 자동완성**(cpk 기존 기능)으로 후보 생성, **1단계·중복제거·개수 상한**.
- 후보 → `tracked` 승격(수동, 또는 상한까지 자동), 나머지 `archived`.

## 6. 수집 (매일 · launchd)

- `tracked` 키워드마다 `cb.search` → 스냅샷 1행 기록. **요청제어(간격·예산·백오프)·프록시 그대로.**
- 기존 `cpk_queue` 로직 재사용(결과를 runs.jsonl 대신/과 함께 SQLite에 적재).
- 시간대 분산, 실패는 다음 날 재개(기존 재개 설계).

## 7. 점수 (투명 · 서브점수 공개)

- **rarity(레어/저경쟁)**: `result_count`↓, 로켓포화율(rocket_cnt/units)↓, 광고밀도(ad_cnt/units)↓, 리뷰 집중도(상위 소수가 리뷰 독점)↑ → 진입 여지.
- **demand(수요)**: `review_sum` 수준 + **리뷰 증가속도**(Δreview_sum / Δday) > 0.
- **steadiness(꾸준함)**: 최근 7일(기본, 조절 가능) 리뷰 증가속도의 **안정성**(변동계수 낮고 지속적 +). ≥ 1~2주 누적돼야 신뢰.
- **opportunity** = 가중합(가중치 조절 가능, 기본 동일가중). 서브점수를 항상 함께 노출(블랙박스 금지).
- **반품**: 점수화하지 않음 → 사람이 후보 보고 판단.

## 8. 도매꾹 소싱 게이트 (존재판별)

- 각 tracked 키워드를 도매꾹 오픈API로 **국내배송 & 우수공급사** 조건 존재판별.
- 요청: `getItemList ver=4.1 market=dome kw=<키워드> sgd=true dfos=false` → `header.numberOfItems>0` 이면 존재.
- 상세 문서: `side-job-ecommerce/domegg/docs/EXISTENCE_CHECK.md` (검증 완료 2026-09-21).
- 대시보드는 **소싱 가능(dome_exists=true) 후보를 우선 노출**. 위탁 목적이면 `market=supply`(도매매)도 병행 확인.
- 한계: 키워드 표기 차이로 0건이 곧 "없음"은 아님 → 동의어/상위어 재질의 고려(비범위, 수동).

## 9. 대시보드 (Railway `web/` 확장, 읽기 전용)

- `/markets`: tracked 표 — opportunity + 서브점수(경쟁/수요/꾸준함) + 리뷰속도 스파크라인 + 도매꾹 소싱뱃지 + 최신 요약. 점수순 정렬, 소싱가능 필터.
- 키워드 상세: 시계열 그래프(리뷰·가격·로켓비율·순위) + 도매꾹 표본 링크.
- 후보 관리: 확장 결과 보기, 승격/보관.
- 데이터 경로: `cpk_market_report.py`(맥미니) → 기존 제한키 SSH(:443) → Railway. (검색 경로와 동일 패턴)

## 10. 테스트 (docs/guidelines 준수)

- **오프라인 유닛(CI)**: 점수 함수(스냅샷→서브점수), 발굴 확장(중복·상한), SQLite 읽기/쓰기(임시 DB), 도매꾹 응답 파서(고정 JSON 픽스처), URL 인코딩.
- **온라인(맥미니 수동)**: 실제 수집 1~2건, 도매꾹 실호출 1건.
- 결함마다 회귀 테스트 추가. "전부 통과 ≠ 버그 없음" — 안 본 것 명시.

## 11. 위험 · 가정 (사실/가정 구분)

- (가정) **리뷰 ≠ 판매**. 수요 프록시일 뿐. (가정) 리뷰증가속도가 판매추세와 상관.
- (사실) **반품률은 쿠팡 검색에 없음** → 사람이 판단(합의됨).
- (사실) **꾸준함은 시계열 필요** → 초기 며칠은 steadiness 미정.
- (위험) 프록시 GB·요청량 = 키워드수 × 주기. 규모 미정 → **최소로 시작(예: 30개·일1회)** 후 확장.
- (위험) 도매꾹 키워드 표기 차이로 존재판별 오탐/누락.

## 12. 미정 (기본값으로 시작, 관측 후 조정)

- 규모/주기: 기본 **~30 키워드 · 하루 1회**. 관측 후 증감.
- 점수 가중치: 기본 동일가중. 실데이터 보고 튜닝.
- 저장 이관: 스냅샷을 runs.jsonl과 병행할지, SQLite 단일로 갈지(구현 시 결정 — SQLite 단일 권장).

## 13. 다음 단계

이 스펙 승인 → superpowers **writing-plans** 스킬로 구현 계획 작성 → 구현. (그 전까지 코드 없음.)
