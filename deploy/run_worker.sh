#!/bin/sh
# The resident worker serves user requests only; no scheduled target requests.
set -eu
cd "$HOME/cpk"
set -a
. ./cpk.env
set +a
case "${CPK_WORKER_ROUTE:-proxy}" in
  direct) unset CPK_PROXY CPK_PROXY_USER CPK_PROXY_PASS ;;
  proxy) ;;
  *) echo 'CPK_WORKER_ROUTE must be direct or proxy' >&2; exit 1 ;;
esac
export CPK_SEARCH_MIN_GAP="${CPK_WEB_SEARCH_MIN_GAP:-5}"
export CPK_WARM_SECS="${CPK_WEB_WARM_SECS:-8}"
exec .venv/bin/python -u cpk_worker.py serve
