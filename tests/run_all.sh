#!/bin/sh
# cpk 테스트 스위트 실행기. cpk 디렉터리에서 실행한다.
#   sh tests/run_all.sh offline   유닛 + 리그레션 (네트워크 없음, 어디서나)
#   sh tests/run_all.sh online    기능 (실제 쿠팡, 맥미니 + 살아있는 쿠키통)
#   sh tests/run_all.sh full      통합 전수 (맥미니 + Chrome, 격리 폴더)
#   sh tests/run_all.sh all       위 전부
set -eu
cd "$(dirname "$0")/.."
PY="${CPK_PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
MODE="${1:-offline}"

run() { echo "=== $1 ==="; "$PY" -m unittest "$2" -v; }

case "$MODE" in
  offline) run "UNIT" tests.test_unit; run "REGRESSION" tests.test_regression ;;
  online)  run "FUNCTIONAL" tests.test_functional ;;
  full)    run "INTEGRATION" tests.test_integration ;;
  all)
    run "UNIT" tests.test_unit
    run "REGRESSION" tests.test_regression
    run "FUNCTIONAL" tests.test_functional
    run "INTEGRATION" tests.test_integration ;;
  *) echo "usage: run_all.sh [offline|online|full|all]"; exit 2 ;;
esac
echo "=== 완료 ($MODE) ==="
