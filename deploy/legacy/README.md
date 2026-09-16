# legacy — 더 이상 쓰지 않는 배포물

현행은 맥미니의 실제 Chrome(브라우저 경로)이다. 아래는 참고용 보관이며 현재 운영에 쓰지 않는다.

- `install.sh`, `cpk-keepalive.service`, `cpk-keepalive.timer` — 구형 Linux(systemd) 설치. 파이썬 curl_cffi 경로 전제라 지금은 검색이 막힌다. macOS Chrome이 없는 서버에서 그대로 돌리면 실패한다.
- `com.cpk.reissue.plist` — 폐기된 파이썬 쿠키 재발급 타이머.
- `com.cpk.collect.plist` — 폐기된 하루 4회 수집 예약.
- `com.cpk.keepalive.plist` — 폐기된 30분마다 검색하는 헬스체크.

2026-09-16부터 사용자 요청으로만 검색한다. `install_mac.sh`는
`disable_scheduled.sh`를 호출해 위 macOS 잡을 disable/bootout 하고 활성 LaunchAgents 폴더에서 치운다.
기존 설치 plist는 `~/cpk/state/disabled-launchagents/`에 보관한다. 여기 있는 타이머는 재설치하지 않는다.
