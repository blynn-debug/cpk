"""브라우저 Cookie 헤더 한 줄을 쿠키통으로 저장하고 곧바로 검증한다.

사용:
  python cpk_import.py cookie.txt      # 파일에서
  echo "<cookie header>" | python cpk_import.py -   # 표준입력에서
"""
from __future__ import annotations

import sys

import cpk_session as cs


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    keep_login = "--keep-login" in sys.argv[2:]
    header = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    n = cs.import_cookie_header(header, keep_login=keep_login)
    cs.log(f"import: 쿠키 {n}개 저장 (로그인 쿠키 {'유지' if keep_login else '제거'})")
    # 검증은 브라우저 경로(공통 관문 통과)로 한다. 중단·예산이면 요청을 보내지 않고 그대로 알린다.
    import cpk_browser as cb
    res = cb.search("계란보관함", kind="import")
    outcome = res["outcome"]
    if outcome in ("paused", "budget"):
        cs.log(f"import: 저장 완료, 검증은 보류({outcome}) — 중단/예산 해제 후 확인")
        return 0
    ok = outcome in ("ok", "no_results")
    cs.log(f"import 검증: outcome={outcome} count={res['count']} -> {'OK' if ok else 'FAIL'}")
    cs.write_health({"ok": ok, "runs": 0, "fails": 0 if ok else 1,
                     "outcome": outcome, "source": "import"})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
