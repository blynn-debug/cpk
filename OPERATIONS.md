# cpk 운영 런북 (OPERATIONS)

장애·정기 작업 대처. 구조 개요는 `web/PRD.md`, 작업 기준은 `CLAUDE.md`.

## 구성 요약
- **맥미니**(집, 주거 IP): 실제 크롤러. 진짜 Chrome + 한국 주거 프록시. `~/cpk/`.
- **aws103**(AWS 서울, 3.38.254.103): 공개 다리. 역터널 종착지, sshd 22·443, 결과 보관 `~/cpk-data`.
- **Railway**: 공개 웹(`web/`). `git push` → 자동배포.
- 접속: `ssh mini`(집 랜) / `ssh mini-remote`(aws103 경유, 외부에서).

## 빠른 상태 점검
```sh
ssh mini-remote 'echo OK'                                  # 맥미니 살아있나(역터널 포함)
curl -s https://cpk-production.up.railway.app/healthz      # 웹 살아있나 → {"ok":true}
curl -s -m 40 https://cpk-production.up.railway.app/api/diag  # 배포버전·aws103 도달·키 존재
ssh aws103 'ss -tln | grep 2222'                           # 역터널 포트 떠 있나
```

## 온디맨드 상주 워커

- 웹의 `/api/jobs`가 짧은 SSH 호출로 작업을 시작하고 조회한다. 실제 검색은 맥미니
  `com.cpk.worker`가 처리한다. 현재 프록시와 검색용 시크릿 컨텍스트를 유지·재사용한다.
- 설치/재시작: `sh deploy/install_worker.sh`. Chrome 본체나 예약 작업은 재시작하지 않는다.
- 설정: `CPK_WORKER_ROUTE=proxy`(기본), `CPK_CONTEXT_TTL=480`, `CPK_REQUEST_TIMEOUT=60`.
  `CPK_WEB_SEARCH_MIN_GAP=5`, `CPK_WEB_WARM_SECS=8`은 기존 웹 설정과 같다.
- 로그: `state/worker.err.log`, `state/performance.jsonl`. Railway의 `cpk.timing` 로그와
  `request_id`로 연결한다. 상태 조회는 추가 검색을 시작하지 않는다.
- 일반 Chrome 로그인·쿠키와 분리된 시크릿 컨텍스트다. 같은 검색용 시크릿 세션은 TTL 안에서
  재사용하므로 검색마다 쿠키를 초기화하는 방식은 아니다. 오류/TTL/연결 종료 때 컨텍스트를 정리한다.
- 재시작은 진행 중 검색을 중단할 수 있다. 작업 상태/소켓 health를 확인하고 진행한다.
- 상세 실측과 복구 절차: [검색 지연 개선 기록](docs/research/2026-09-22-latency-implementation.md).

## 증상별 대처

### 1. 웹에서 "맥미니에 연결하지 못했어요"(transport)
원인 후보 순서로 확인:
1. **역터널 죽음**: `ssh mini-remote 'echo OK'` 실패 → 맥미니가 네트워크 바뀐 뒤 재접속 중이거나, aws103에 죽은 터널 포트가 남음.
   - aws103의 stale 포트 정리: `ssh aws103 'sudo fuser -k 2222/tcp'` → launchd가 새 터널을 다시 바인딩(수십 초).
2. **Railway→aws103 포트 문제**: `/api/diag` 의 `tcp_aws103` 확인. Railway는 **아웃바운드 22를 막으므로 반드시 443** 사용(`AWS103_PORT=443`). aws103 보안그룹에 443 인바운드(0.0.0.0/0) 열려 있어야 함.
3. **맥미니 다운/절전**: 집에서 직접 확인. 상시 Chrome(launchd com.cpk.chrome) 살아있나.

### 2. 검색이 "시간 초과"(timeout)
- 작업 경로는 대기열 포함60초 마감, SSH 시작/조회는 각20초 상한이다. 기존 동기 API는 기존
  `SSH_TIMEOUT`(170), gunicorn `--timeout 190`을 유지한다.
- 2026-09-22 격리 워커 5건은 첫 검색34.7초, 재사용4.9~7.5초였다. 실서비스 보장 시간이 아니다.
- `performance.jsonl`에서 대기열/홈/워밍/검색/자동완성을 구분한다. 입력창 출현만으로 워밍 완료를
  판단하면 실패한 실측이 있어 워밍을 무작정 줄이지 않는다.

### 3. 검색이 "차단"(challenge/http_error) 또는 결과 0
- **error403(하드)**: 출구 IP 평판 저하. 프록시가 IP를 회전하므로 재시도로 대개 통과. 계속되면 프록시 잔여 GB·국가(KR) 설정 확인.
- **sec-cpt(소프트)**: `_abck` 미검증. 워밍 시간(`CPK_WARM_SECS`)이 짧지 않은지 확인.
- 공용 중단(백오프)이 걸렸는지: `ssh mini 'cat ~/cpk/state/control.json'` → `paused_until` 확인. 필요시 `.venv/bin/python -c "import cpk_session as cs; cs.clear_pause()"`.

### 4. 프록시 GB 소진 / 자격증명 교체
- Proxy-Cheap 대시보드에서 잔여 GB 확인. 소진되면 검색이 전부 실패.
- 자격증명 교체: 맥미니 `~/cpk/cpk.env` 의 `CPK_PROXY` 갱신(형식 `http://user:pass_country-KR@thehub.proxy-cheap.com:8080`). 비밀번호에 `_country-KR` 유지. sticky/ttl은 코드가 자동 부가.

### 5. Railway 배포가 최신이 아님
- `/api/diag` 의 `version` 이 코드와 다르면 미배포. Auto deploy가 켜져 있으면 push로 자동, 아니면 Settings→Source→**Enable** 또는 **Check for updates**.
- 롤백: Railway Deployments 탭에서 이전 성공 배포 → Redeploy.

## 정기/수동 작업
- **웹 배포**: `git push`(자동). 로컬 작업본 `C:\Users\djliz\cpk`.
- **맥미니 코드 배포**: `sh deploy/push_to_mini.sh` (비밀·상태 제외하고 크롤러 코드만 전송).
- **오프라인 테스트**: `python -m unittest tests.test_unit tests.test_regression` + `cd web && python -m unittest tests.test_web`. push 시 CI가 자동 실행.
- **온라인 테스트(실제 쿠팡 소비, 맥미니에서만)**: `sh tests/run_all.sh online` — 운영 중단(backoff) 아닐 때만.

## 비밀 목록 (커밋 금지)
| 비밀 | 위치 |
|---|---|
| 프록시 자격증명 | 맥미니 `~/cpk/cpk.env` (`CPK_PROXY`) |
| 쿠팡 쿠키통 | 맥미니 `~/cpk/state/` |
| Railway용 SSH 키 | Railway env `SSH_KEY`, 로컬 `~/.ssh/cpk_web` |
| aws103/맥미니 접속 키 | 로컬 `~/.ssh/aws103` |
