# cpk-web

키워드 1개를 입력하고 버튼을 누르면 쿠팡 검색 결과를 화면에 보여주는 간단한 웹. Railway 배포용.
설계는 [PRD.md](PRD.md) 참고.

## 구조
- **크롤링은 맥미니에서만** 실행된다(진짜 Chrome + KR 주거 프록시). Railway는 화면 + SSH 중계만.
- `버튼 1회 = 키워드 1건`. 요청 제어(간격·일일예산·차단 중단)는 맥미니 `control.json`이 그대로 강제.

```
브라우저 → Railway(Flask) → SSH(aws103 ProxyJump, 기존 :22) → 맥미니 cpk_search_json.py → JSON
```

## 로컬 실행(개발)
```sh
cd web
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export AWS103_HOST=<aws103 IP>  SSH_KEY_FILE=~/.ssh/cpk_web   # 또는 SSH_KEY 에 키 내용
python app.py    # http://localhost:8000
```

## 테스트(오프라인, 네트워크·SSH 없음)
```sh
cd web && python -m unittest tests.test_web
```

## Railway 배포
1. Railway 새 프로젝트 → Deploy from GitHub → `blynn-debug/cpk` 선택.
2. **Settings → Root Directory = `web`**. (빌드는 `web/`만; nixpacks 가 openssh-client 포함)
3. **Variables** 설정:
   | 변수 | 값 |
   |---|---|
   | `SSH_KEY` | 전용 개인키 전체(PEM, 여러 줄) |
   | `AWS103_HOST` | aws103 공인 IP |
   | `AWS103_USER` | `ec2-user` |
   | `MINI_USER` | `mini_worker` |
   | `MINI_TUNNEL_PORT` | `2222` |
   | `APP_PASSWORD` | (선택) 접근 비밀번호 |
4. 배포 후 도메인 접속 → 키워드 검색.

### 전용 SSH 키(보안)
Railway 에 넣는 키는 아래처럼 제한된다(유출돼도 검색·터널포워딩만 가능):
- 맥미니 `~/.ssh/authorized_keys`: `command="~/cpk/deploy/cpk_ssh_search.sh",no-port-forwarding,no-pty,...`
- aws103 `~/.ssh/authorized_keys`: `permitopen="127.0.0.1:2222",no-pty,...`

## 응답 시간
1건 ≈ 15~30초(홈 워밍업 + _abck + 검색 + 필요 시 재시도). gunicorn/프런트 타임아웃 60~90초.
