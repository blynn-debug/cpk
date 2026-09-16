#!/bin/sh
# 차단 구간이 오면 url vs ui 검색을 자동 비교하는 감시기.
# 30분마다 url 로 가볍게 프로브하다가 차단(challenge/http_error)이 잡히면 그 순간 url·ui 를 비교 기록한다.
# 실행: cd ~/cpk && nohup sh tools/watch_url_vs_ui.sh >/dev/null 2>&1 &
# 결과: ~/cpk/state/url_vs_ui.log (aws103:cpk-data/url_vs_ui.log 동기화)
cd "$(dirname "$0")/.."
set -a; . ./cpk.env 2>/dev/null; set +a
export CPK_HOME="$HOME/cpk/state" PYTHONIOENCODING=utf-8
PY=.venv/bin/python
L="$CPK_HOME/url_vs_ui.log"
DEADLINE=$(( $(date +%s) + 12*3600 ))
GAP="${WATCH_GAP:-1800}"

say() { echo "$(date '+%F %T') $*" >> "$L"; rsync -az --timeout=60 "$L" aws103:cpk-data/ 2>/dev/null || true; }

say "url_vs_ui 감시 시작 (${GAP}s 간격, 최대 12h)"
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  u=$("$PY" probe_compare.py url 2>/dev/null | tail -1)
  denied=$(printf '%s' "$u" | "$PY" -c "import json,sys
try:
    d=json.load(sys.stdin)['url']; print('1' if d.get('outcome') in ('challenge','http_error') else '0')
except Exception:
    print('0')" 2>/dev/null || echo 0)
  if [ "$denied" = "1" ]; then
    say "차단 감지(url): $u"
    both=$("$PY" probe_compare.py both 2>/dev/null | tail -1)
    say "url vs ui 비교: $both"
  else
    say "url 정상: $u"
  fi
  sleep "$GAP"
done
say "감시 종료(12h 경과)"
