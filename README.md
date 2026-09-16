# cpk — 쿠팡 검색 조회(브라우저 경로) + 요청 제어

집 맥미니의 실제 Chrome으로 쿠팡 검색 결과를 받아 파싱한다. **사용자가 요청할 때만** 단건 검색 또는 키워드 목록의 일괄 수집을 실행한다. 예약 수집과 검색을 만드는 주기적 헬스체크는 사용하지 않는다.

- 목적·문제 정의: [PROBLEM.md](PROBLEM.md)
- 코드·운영 리뷰: [REVIEW_2026-09-16.md](REVIEW_2026-09-16.md)

## 어떻게 동작하나

- 검색은 맥미니의 상시 Chrome(전용 프로필, CDP 9222)에서 페이지를 열어 HTML을 받아 파싱한다.
  파이썬 직접 요청(curl_cffi)은 아카마이가 TLS 지문 등으로 자주 막으므로 보조 경로로만 남겨 둔다.
  (원인은 단정하지 않는다 — PROBLEM.md의 가설/확정 구분 참고.)
- 모든 검색 시도는 공통 요청 관문(`cs.request_gate`)을 지난다: 중단 상태 확인 + 요청 집계 + 동시 금지·최소 간격.
- 검색 결과는 유형으로 구분한다: `ok`(상품 있음) / `no_results`(정상 무결과) / `challenge`(봇 검증) /
  `http_error` / `load_error`. 실패는 `state/evidence/`에 상태·URL·제목·HTML 일부를 남긴다.
- 명확한 차단(챌린지·HTTP 403/429)이면 **공통 중단**을 걸어 수집·온디맨드가 함께 쉰다.

## 필수: 연관검색어 + 자동완성 (2026-09-14 지침)

상품 목록보다 먼저 보는 것이 이 둘이다. 쿠팡이 실제 검색·구매 로그로 만든 검색어라 상위노출과 판매로 직결된다.

| 항목 | 출처 | 비고 |
|---|---|---|
| 연관검색어 | 결과 페이지의 `srp_relatedKeywords` 블록 `<a title>` | 검색어에 따라 없을 수 있음 |
| 자동완성 | `GET /n-api/web-adapter/search?keyword=…` (JSON) | 10개. 검색 페이지가 막혀도 이 API는 대체로 살아 있음 |

`total_count`는 비로그인 세션에서 60으로 잘려 나온다. 시장 크기 판단에 쓰지 말 것.

## 파일

| 파일 | 역할 |
|---|---|
| `cpk_browser.py` | 상시/전용 Chrome·CDP, 브라우저 검색(`search`/`search_session`), 결과 유형 분류·증거 보존, 구형 쿠키 재발급 |
| `cpk_session.py` | 쿠키통, HTTP 클라이언트, 로그, 헬스, 파일 잠금, **공통 요청 제어**(request_gate·pause·예산·간격) |
| `cpk_search.py` | BeautifulSoup 파서(가격·배지·리뷰·광고·연관검색어). 구형 검색 CLI는 폐기 |
| `cpk_collect.py` | 검색어 순회 수집, 결과 저장·동기화, 실패/무결과 기록, `--status` |
| `cpk_keepalive.py` | 수동 진단용 검색 헬스체크. 예약 실행하지 않음 |
| `cpk_keywords.py` | 검색어 확장(연관검색어 + 자동완성) |
| `cpk_import.py`, `push_cookie.ps1` | 수동 쿠키 주입(browser 검색에는 필수 아님) |
| `keywords.txt`, `push_keywords.ps1` | 수집 검색어와 전송(집/외부 자동 선택) |
| `deploy/` | 상시 Chrome launchd 유닛, `install_mac.sh`, 예약 작업 해제 스크립트 |
| `deploy/legacy/` | 구형 Linux systemd 및 폐기된 수집·헬스체크·재발급 타이머(참고용, 미사용) |
| `tests/` | 오프라인(유닛·회귀) + 온라인·통합(차단 구간 자동 skip) |

상태 폴더 `~/cpk/state/`에는 `jar.json`, `health.json`, `control.json`(중단·예산), `keepalive.log`,
`evidence/`(실패 증거), `chrome-profile/`이 생긴다. 이 폴더와 `cpk.env`는 git에 넣지 않는다.

## 배포 (맥미니)

```powershell
# 1. 코드 복사 (집 와이파이면 mini, 밖이면 mini-remote)
scp cpk_*.py requirements.txt cpk.env.example README.md mini:~/cpk/
scp deploy/com.cpk.chrome.plist deploy/install_mac.sh deploy/disable_scheduled.sh mini:~/cpk/deploy/
# 2. 설치 (venv + requirements + Chrome 잡 1개; 없으면 cpk.env 를 예시에서 생성)
ssh mini "sh ~/cpk/deploy/install_mac.sh"
```

launchd 잡은 `com.cpk.chrome`(상시 Chrome)만 설치한다. 예전 `com.cpk.collect`,
`com.cpk.keepalive`, `com.cpk.reissue`는 설치 시 disable/bootout 하고 설치 plist를 보관 폴더로 옮긴다.
기존 운영에서 예약만 해제하려면 `sh ~/cpk/deploy/disable_scheduled.sh`를 실행한다.

## 사용

```bash
# 온디맨드 검색(필요할 때)
ssh mini "cd ~/cpk && set -a && . ./cpk.env && set +a && CPK_HOME=~/cpk/state .venv/bin/python cpk_browser.py search '방충망 잠금장치'"
# 상태(결과·대기·실패·신선도)
ssh mini "cd ~/cpk && CPK_HOME=~/cpk/state .venv/bin/python cpk_collect.py --status"
# 키워드 목록 일괄 수집(사용자가 요청할 때 실행; 별도 예약/자동 재개 없음)
ssh mini "cd ~/cpk && set -a && . ./cpk.env && set +a && CPK_HOME=~/cpk/state .venv/bin/python cpk_collect.py"
# 로그
ssh mini "tail -20 ~/cpk/state/keepalive.log"
# 밖에서 결과만
ssh aws103 "tail -5 ~/cpk-data/runs.jsonl"
```

검색어 추가: 로컬 `keywords.txt`를 고친 뒤 `powershell -File push_keywords.ps1`(집/외부 자동 선택).
한 줄에 하나, `검색어 | pages=2`로 페이지 지정.

## 요청 제어(핵심)

- `CPK_SEARCH_MIN_GAP`(기본 20초): 검색 사이 최소 간격. 동시 검색은 금지된다.
- `CPK_DAILY_BUDGET`(기본 300): 하루 총 관문 요청 예산(검색·자동완성·워밍업·재시도 합산). 키워드 수가 아니다.
- `CPK_BACKOFF_HOURS`(기본 6): 차단 시 공통 중단 시간. 이 값들은 현재 설정일 뿐 안전이 검증된 기준은 아니다(PROBLEM.md).
- 중단 상태에서는 새 검색·재시도·워밍업을 시작하지 않는다. `--status`의 `paused_remaining_sec`로 확인한다.

전체 환경 변수는 [cpk.env.example](cpk.env.example) 참고.

## 100개 이상 키워드

한 키워드당 1페이지와 자동완성을 조회하면, 한 브라우저 세션의 기본 관문 요청 수는
`워밍업 1 + 검색 N + 자동완성 N = 1 + 2N`이다(모든 검색 성공, 축약/재시도 없음 가정).
100개는 201건, 150개는 301건이므로 기본 하루 예산 300으로는 150개 전체가 끝나지 않는다.
추가 페이지·축약 자동완성·재시도와 이미 사용한 예산도 계산해야 한다. 브라우저가 따로 받는
이미지·스크립트 등의 HTTP 전송 수/바이트는 이 관문 집계와 다르다.

201건 사이 최소 20초 간격만 합쳐도 약 67분이고, 수집기의 키워드 간 25~60초 대기와
실제 로딩·처리·재시도 때문에 더 걸린다. 이는 설정에서 계산한 값이며 100개 실측 완료 시간이 아니다.
100개 이상이라는 개수만으로 유료 프록시가 필수라는 근거는 없다. 현재는 직접 연결의
100개 연속 성공을 검증하지 않았고, 예산이나 간격 값이 차단을 피하는 안전 기준으로 검증된 것도 아니다.

100개 이상을 안정적으로 처리하려면 사용자 요청으로 만든 작업 목록, 키워드/페이지별 완료 기록,
중단 후 미완료 항목부터 재개, 진행률·남은 예산 표시가 필요하다. 현재 순회 수집은 결과를 개별 저장하지만
이런 영속 작업 큐/재개 기능은 아직 없다. 프록시 도입은 필요한 완료 시간과 직접 연결의 관측 결과를 보고 판단한다.

## 테스트

```bash
sh tests/run_all.sh offline   # 유닛 + 회귀 (네트워크 없음, 어디서나)
sh tests/run_all.sh online    # 실제 쿠팡(맥미니). 차단 구간이면 자동 skip
sh tests/run_all.sh full      # 통합(맥미니 + Chrome)
```

## 쿠키가 끊겼을 때(자동 재발급이 실패할 때만)

브라우저 경로는 보통 쿠키를 스스로 관리한다. 그래도 필요하면 브라우저에서 Cookie 헤더를 복사해
`push_cookie.ps1`로 넣는다(파이썬 경로 보조).

## 주의

- 쿠팡 약관은 자동 접근을 제한한다. 개인 조사용 저빈도로만 쓴다.
- `CPK_PROXY`는 현재 파이썬 경로에만 적용되고 Chrome 검색에는 적용되지 않는다(미구현).
