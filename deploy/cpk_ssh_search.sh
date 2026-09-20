#!/bin/sh
# 맥미니 authorized_keys forced-command 로 지정되는 래퍼.
# ssh <전용키> mini-remote '<키워드>' 로 호출되면 sshd 가 SSH_ORIGINAL_COMMAND 에 키워드를 담고
# 이 스크립트를 실행한다. 키워드는 env 로만 전달돼 셸 해석을 타지 않는다(안전).
# cpk.env 를 로드해 프록시 등 설정을 적용한 뒤 검색 1건을 JSON 으로 낸다.
set -eu
cd "$HOME/cpk" || exit 1
set -a
. ./cpk.env 2>/dev/null || true
set +a
# 웹(온디맨드) 검색은 응답을 빠르게: 요청 간격·워밍을 낮춰 cpk.env 값보다 우선 적용한다.
# (큐/헬스체크 등 다른 경로는 cpk.env 의 보수적 값을 그대로 쓴다 — 여기 export 는 이 프로세스에만 유효)
export CPK_SEARCH_MIN_GAP="${CPK_WEB_SEARCH_MIN_GAP:-5}"
export CPK_WARM_SECS="${CPK_WEB_WARM_SECS:-8}"
# 리포트 sentinel 이면 시장 리포트로, 아니면 기존 검색 1건으로 분기한다.
if [ "${SSH_ORIGINAL_COMMAND:-}" = "__market_report__" ]; then
  exec .venv/bin/python cpk_market_report.py
fi
exec .venv/bin/python cpk_search_json.py
