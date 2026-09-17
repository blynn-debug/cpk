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
exec .venv/bin/python cpk_search_json.py
