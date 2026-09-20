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

## 증상별 대처

### 1. 웹에서 "맥미니에 연결하지 못했어요"(transport)
원인 후보 순서로 확인:
1. **역터널 죽음**: `ssh mini-remote 'echo OK'` 실패 → 맥미니가 네트워크 바뀐 뒤 재접속 중이거나, aws103에 죽은 터널 포트가 남음.
   - aws103의 stale 포트 정리: `ssh aws103 'sudo fuser -k 2222/tcp'` → launchd가 새 터널을 다시 바인딩(수십 초).
2. **Railway→aws103 포트 문제**: `/api/diag` 의 `tcp_aws103` 확인. Railway는 **아웃바운드 22를 막으므로 반드시 443** 사용(`AWS103_PORT=443`). aws103 보안그룹에 443 인바운드(0.0.0.0/0) 열려 있어야 함.
3. **맥미니 다운/절전**: 집에서 직접 확인. 상시 Chrome(launchd com.cpk.chrome) 살아있나.

### 2. 검색이 "시간 초과"(timeout)
- 크롤이 재시도·느린 IP로 길어짐. 정상 ~50초, 재시도 붙으면 ~90초+.
- 웹 상한: Railway env `SSH_TIMEOUT`(170), Dockerfile gunicorn `--timeout 190`.
- 자주 나면: 웹 속도 튜닝값(`deploy/cpk_ssh_search.sh` 의 `CPK_SEARCH_MIN_GAP`/`CPK_WARM_SECS`) 조정, 또는 프록시 상태 점검.

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
