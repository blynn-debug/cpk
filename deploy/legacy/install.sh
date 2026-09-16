#!/usr/bin/env bash
# aws103(Amazon Linux 2023)에 cpk 설치/갱신. 서버에서: bash ~/cpk/deploy/install.sh
set -euo pipefail
APP="$HOME/cpk"
cd "$APP"
mkdir -p state
chmod 700 state

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q curl_cffi beautifulsoup4

# 환경 파일(ntfy 토픽 등). 없으면 빈 파일 생성.
[ -f cpk.env ] || printf 'CPK_NTFY_TOPIC=\nCPK_CANARY_EVERY=4\n' > cpk.env
chmod 600 cpk.env

# systemd 유닛 설치 (경로에 홈 디렉터리 치환)
sed "s|__HOME__|$HOME|g" deploy/cpk-keepalive.service | sudo tee /etc/systemd/system/cpk-keepalive.service >/dev/null
sudo cp deploy/cpk-keepalive.timer /etc/systemd/system/cpk-keepalive.timer
sudo systemctl daemon-reload
sudo systemctl enable --now cpk-keepalive.timer

echo "installed. timer:"; systemctl list-timers --no-pager | grep cpk || true
echo "다음: 브라우저 Cookie 헤더를 넣으려면 로컬에서 push_cookie.ps1 실행"
