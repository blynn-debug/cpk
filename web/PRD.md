# cpk-web PRD — 키워드 1건 온디맨드 검색 웹

## 1. 목적
브라우저에서 키워드를 입력하고 버튼을 누르면, 쿠팡 검색 결과(상품 목록·로켓 뱃지·연관검색어·자동완성)를
화면에 보여주는 간단한 웹. 배포는 Railway.

## 2. 범위
- **버튼 클릭 1회 = 키워드 1건 조회.** 연속·대량 조회는 하지 않는다(과다요청·차단 위험 회피).
- 결과는 화면에 표(상품명·가격·뱃지·리뷰·링크) + 연관검색어 + 자동완성으로 표시.
- 로그인·회원·저장 기능 없음. 단일 페이지.

## 3. 비범위(하지 않음)
- Railway 컨테이너에서 직접 크롤링하지 않는다(데이터센터 IP는 아카마이 차단, 컨테이너 Chrome 미검증).
- 배치·스케줄·큐 트리거 없음(그건 맥미니 `cpk_queue.py` 몫).
- 이미지 프록시 없음(썸네일은 브라우저가 쿠팡 CDN에서 직접 로드).

## 4. 아키텍처
```
브라우저 ── HTTPS ──> Railway(Flask: UI + /api/search)
                          │  SSH (기존 aws103:22 open, 신규 포트 없음)
                          ▼
                     aws103 (ProxyJump) ──역터널(2222)──> 맥미니
                          │
                          ▼
        맥미니 cpk 크롤러(진짜 Chrome + KR 주거 프록시) → cb.search(kw) → JSON
```
- **크롤링은 검증된 맥미니 경로 재사용.** Railway는 화면과 중계만.
- 요청 제어(간격·일일예산·차단 시 중단)는 맥미니 `control.json`이 그대로 강제 → 웹에서 눌러도 안전.

## 5. 보안
- Railway에는 **전용 SSH 키**만 저장. 그 키는:
  - 맥미니 authorized_keys에서 **forced-command**(검색 스크립트만 실행, 포트포워딩·pty 금지)로 제한.
  - aws103 authorized_keys에서 **permitopen=127.0.0.1:2222**(맥미니 터널로만 포워딩, 셸 금지)로 제한.
  - → 키가 유출돼도 "검색 1건 실행"과 "맥미니 터널 포워딩" 외에는 못 함.
- 웹 접근은 선택적 `APP_PASSWORD`로 보호(설정 시 조회 전 비밀번호 요구). 미설정 시 공개.
- 크롤 남용은 맥미니 일일예산·간격이 상한을 건다.

## 6. 인터페이스
### 6.1 웹 → 서버
`POST /api/search` `{ "q": "<키워드>" }` → JSON:
```json
{ "query": "...", "outcome": "ok|no_results|challenge|http_error|load_error|paused|budget|busy|error",
  "count": 60, "total_count": 60, "badge_counts": {"로켓":28,...},
  "related_keywords": ["..."], "autocomplete": ["..."],
  "items": [{"name","price","fee","reviews","badge","ad","product_id","url"}],
  "elapsed_ms": 21000, "error": null }
```
### 6.2 서버 → 맥미니
`ssh <dedicated-key> mini-remote` → forced-command → `cpk_search_json.py`가 키워드(=SSH_ORIGINAL_COMMAND)를
받아 `cb.search`를 돌리고 위 JSON을 stdout으로 출력.

## 7. UX
- 입력창 + [검색] 버튼. 누르면 버튼 비활성화 + 로딩 표시("검색 중… 최대 30초").
- 완료 시: 요약(총 건수·뱃지 분포) → 상품 표 → 연관검색어 칩 → 자동완성 칩.
- 실패 시 사람이 읽을 메시지:
  - `challenge`/`http_error`: "지금 차단 구간이라 실패. 잠시 후 다시" 
  - `paused`/`budget`: "요청 제어로 대기 중(오늘 한도/중단). 나중에 다시"
  - `busy`: "다른 검색이 진행 중"
  - `error`/타임아웃: "연결 오류"
- 응답 지연: 1건 ≈ 15~30초(홈 워밍업 + _abck + 검색 + 재시도). 프런트 타임아웃 60초.

## 8. 성공 기준
- 정상 키워드 조회 시 상품 표·연관검색어·자동완성이 화면에 뜬다.
- 차단/대기/오류가 사용자 문구로 구분돼 뜬다.
- Railway 재배포·재시작 후에도 동작(무상태).
- 테스트: 오프라인 유닛(입력검증·SSH명령 구성·결과 정형·실패 매핑, ssh 목) + 온라인 1건(맥미니 실호출).

## 9. 배포(Railway)
- GitHub 레포 `blynn-debug/cpk`의 `web/` 디렉터리를 root로 빌드(Python/Flask, gunicorn).
- 컨테이너에 openssh-client 필요(nixpacks 설정).
- 환경변수: `SSH_KEY`(전용 개인키), `AWS103_HOST`, `AWS103_USER=ec2-user`, `MINI_USER=mini_worker`,
  `MINI_TUNNEL_PORT=2222`, (선택)`APP_PASSWORD`.
- 시작: `gunicorn app:app`.
