#!/bin/sh
# 아카마이 차단이 풀리면 온라인·통합 테스트를 자동 실행하는 일회성 감시기.
# 최대 6시간, 30분 간격으로 세션을 프로브해서 열리는 즉시 테스트를 돌리고 결과를 로그에 남긴다.
# 실행: cd ~/cpk && nohup sh tests/online_watch.sh >/dev/null 2>&1 &
# 결과: ~/cpk/state/online_test.log  (aws103:cpk-data/online_test.log 로도 동기화)
cd "$(dirname "$0")/.."
set -a; . ./cpk.env 2>/dev/null; set +a
export CPK_HOME="$HOME/cpk/state" PYTHONIOENCODING=utf-8
LOG="$CPK_HOME/online_test.log"
PY=.venv/bin/python
DEADLINE=$(( $(date +%s) + 6*3600 ))

say() { echo "$(date '+%F %T') $*" >> "$LOG"; rsync -az --timeout=60 "$LOG" aws103:cpk-data/ 2>/dev/null || true; }

say "online-watch 시작 (최대 6h, 30분 간격)"
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  ok=$("$PY" -c "import cpk_browser as cb; print(1 if cb.reissue(verify=True) else 0)" 2>/dev/null | tail -1)
  if [ "$ok" = "1" ]; then
    say "세션 열림 — 온라인/통합 테스트 실행"
    {
      echo "== ONLINE =="; sh tests/run_all.sh online 2>&1 | grep -iE "Ran|OK|FAIL|ERROR|skip"
      echo "== FULL ==";   sh tests/run_all.sh full   2>&1 | grep -iE "Ran|OK|FAIL|ERROR|skip"
    } >> "$LOG" 2>&1
    say "online-watch 완료"
    exit 0
  fi
  say "아직 차단 구간(reissue=$ok) — 30분 후 재시도"
  sleep 1800
done
say "6h 내 세션이 열리지 않음 — 종료"
