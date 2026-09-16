#!/bin/sh
# 사용자 요청으로만 조회한다. 기존 예약 수집/검색 헬스체크/재발급을 해제한다.
# Chrome과 SSH 터널은 건드리지 않는다. 재실행해도 예약 작업을 되살리지 않는다.
set -eu
CPK_UID="$(id -u)"
CPK_ARCHIVE="$HOME/cpk/state/disabled-launchagents"
mkdir -p "$CPK_ARCHIVE"
chmod 700 "$CPK_ARCHIVE"

for job in com.cpk.collect com.cpk.keepalive com.cpk.reissue; do
  target="gui/$CPK_UID/$job"
  launchctl disable "$target"
  if launchctl print "$target" >/dev/null 2>&1; then
    launchctl bootout "$target"
  fi
  for _ in 1 2 3 4 5; do
    launchctl print "$target" >/dev/null 2>&1 || break
    sleep 1
  done
  if launchctl print "$target" >/dev/null 2>&1; then
    echo "ERROR: $job is still registered" >&2
    exit 1
  fi
  plist="$HOME/Library/LaunchAgents/$job.plist"
  if [ -f "$plist" ]; then
    mv "$plist" "$CPK_ARCHIVE/$job.$(date +%Y%m%dT%H%M%S).$$.plist"
  fi
  echo "$job: disabled, unregistered, no scheduled requests"
done
