#!/bin/sh
# 맥미니에 cpk 설치/갱신. 맥미니에서: sh ~/cpk/deploy/install_mac.sh
# launchd 잡: com.cpk.chrome(상시 Chrome)만 설치한다.
# 검색/일괄 수집은 사용자가 요청할 때 실행한다. 예약 검색 잡은 해제한다.
set -eu
APP="$HOME/cpk"
cd "$APP"
mkdir -p state data
chmod 700 state
sh deploy/disable_scheduled.sh

PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$c" ] && { PY="$c"; break; }
done
[ -n "$PY" ] || { echo "python3 없음"; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
if [ -f requirements.txt ]; then
  .venv/bin/pip install -q -r requirements.txt
else
  .venv/bin/pip install -q curl_cffi beautifulsoup4 websocket-client
fi

# 환경 파일. 있으면 건드리지 않는다. 없으면 예시에서 복사한다(단일 소스: cpk.env.example).
if [ ! -f cpk.env ]; then
  if [ -f cpk.env.example ]; then
    cp cpk.env.example cpk.env
  else
    printf 'CPK_COLLECT_MODE=browser\nCPK_QUIET_HOURS=\nCPK_SYNC_TARGET=aws103:cpk-data/\n' > cpk.env
  fi
fi
chmod 600 cpk.env

mkdir -p "$HOME/Library/LaunchAgents"
UID_="$(id -u)"
for job in com.cpk.chrome; do
  PL="$HOME/Library/LaunchAgents/$job.plist"
  sed "s|__HOME__|$HOME|g" "deploy/$job.plist" > "$PL"
  launchctl bootout "gui/$UID_/$job" 2>/dev/null || true
  # bootout 직후 프로세스(특히 Chrome)가 완전히 내려가길 기다렸다 bootstrap 한다.
  for _ in 1 2 3 4 5; do
    launchctl print "gui/$UID_/$job" >/dev/null 2>&1 || break
    sleep 1
  done
  launchctl bootstrap "gui/$UID_" "$PL" 2>/dev/null || { sleep 2; launchctl bootstrap "gui/$UID_" "$PL"; }
  launchctl enable "gui/$UID_/$job"
done

echo "installed. python: $(.venv/bin/python --version)"
for job in com.cpk.chrome; do
  st="$(launchctl print "gui/$UID_/$job" 2>/dev/null | grep 'state =' | head -1 | sed 's/^[[:space:]]*//')"
  echo "$job: ${st:-?}"
done
echo "수집/헬스체크/재발급 예약 없음. 검색은 사용자 요청으로만 실행합니다."
echo "검색어: $(grep -cv '^\s*#\|^\s*$' keywords.txt 2>/dev/null || echo 0)개 (keywords.txt)"
