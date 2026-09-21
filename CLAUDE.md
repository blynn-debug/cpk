# cpk — 프로젝트 지침 (핵심축)

Claude Code가 세션 시작 시 자동으로 읽는 핵심축이다. 세부·근거는 `docs/guidelines/` 참조.
규칙은 "한다/하지 않는다", 판단 재료는 "고려한다".

최근 운영 변경: [2026-09-22 검색 워커 인수인계](docs/handoff/2026-09-22-search-worker.md).
시크릿 세션 재사용·진행형 UI·403 대기 수정이 배포됐다. 실패 표본과 남은 검증도 함께 확인한다.

## 무엇인가
쿠팡 온디맨드 검색 시스템. **얼굴** = Railway(`web/`), **다리** = aws103(역터널·중계), **손발** = 집 맥미니(진짜 Chrome + 한국 주거 프록시). 설계: `web/PRD.md`.

## 작업 방식 — 사용자 개입 우선 (자동화는 관측·되돌림 가능하게)
- 되돌리기 어렵거나 외부로 나가는 행동(배포, 삭제, 보안그룹·SSH키·프록시 변경, 대량 크롤)은 **먼저 알리고 승인받는다.**
- **계획을 먼저 제시하고 승인 후 실행.** 자동화는 언제든 사람이 보고·중단·되돌릴 수 있게 만든다.
- **가설과 사실을 구분한다.** 실측 없이 단정하지 않고, "무엇을 확인했고 무엇을 안 봤는지"를 함께 보고한다.

## 코딩 — `docs/guidelines/coding-guidelines-full.md`
- 착수 전 목표를 한 줄로 쓰고, 경계(입력 0개/1개/비정상/중복)를 먼저 따진다.
- **기존 코드베이스의 용어·패턴·파라미터 순서를 조사해 따른다.** 새 이름·구조를 지어내면 이유를 보고한다.
- 변경 가능성이 높은 것(외부 API·정책 상수·포맷)은 모듈 내부에 숨긴다.

## 테스트 — 활성 기준: `docs/guidelines/testing-guidelines-generic.md`
- `testing-guidelines.md` 는 **다른 프로젝트(트레이딩봇) 특화 예시**다. cpk 규칙이 아니라 참고용.
- **스위트**
  - 오프라인(네트워크 없음, 어디서나·CI): `tests/test_unit`, `tests/test_regression`, `web/tests/test_web`.
  - 온라인(맥미니에서만 수동, 실제 쿠팡 소비): `tests/test_functional`, `tests/test_integration`.
- **"전부 통과"를 "버그 없음"으로 보고하지 않는다** — 안 본 것을 함께 쓴다.
- 결함을 고치면 **그 결함용 회귀 테스트를 추가한다.** 판정은 운영과 같은 경로(브라우저)로 한다.
- CI가 push마다 오프라인 스위트를 돌린다(`.github/workflows/ci.yml`). 초록이 아니면 배포로 넘기지 않는다.

## 배포·운영
- **정본**: github.com/blynn-debug/cpk. **로컬 작업본**: `C:\Users\djliz\cpk` (여기서 작업·push).
- **웹**: `git push` → **Railway 자동배포**(`web/`, Root Directory=web, Dockerfile). 되돌리기는 이전 배포로 롤백.
- **맥미니**: `cpk_*.py` 변경은 `scp` 로 `~/cpk` 에 배포(수동). 접속: `ssh mini`(집 랜) / `ssh mini-remote`(aws103 경유).
- **비밀**: `cpk.env`(맥미니), Railway 환경변수, 제한된 SSH 키(`~/.ssh/cpk_web`). **커밋 금지**(`.gitignore` 확인). Railway→aws103 은 포트 443(22 차단 우회).

## 설치된 스킬 (해당 작업 때 활용)
`python-project-structure` · `python-testing-patterns` · `python-code-style` · `webapp-testing`
