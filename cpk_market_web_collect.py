"""웹 검색창(온디맨드) 수집: 키워드 1건을 수집(병렬 경로, 워커 1)해 저장한 뒤,
갱신된 마켓 리포트 JSON 한 줄을 stdout 으로 낸다. 대시보드가 그대로 다시 그린다.

키워드는 SSH_ORIGINAL_COMMAND 에서 온다: forced-command 가 '__collect__ <키워드>' 를 넘긴다.
키워드 값은 셸 해석을 타지 않고 여기(파이썬)에서만 파싱·검증한다(cpk_search_json.py 와 동일 원칙).
항상 파싱 가능한 JSON 한 줄만 출력한다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

PREFIX = "__collect__"
ALLOWED = re.compile(r"^[\w가-힣ㄱ-ㅎㅏ-ㅣ0-9 ().,+&/-]{1,60}$")


def _keyword() -> str:
    raw = (os.environ.get("SSH_ORIGINAL_COMMAND", "") or "").strip()
    if raw.startswith(PREFIX):
        raw = raw[len(PREFIX):].strip()
    if len(sys.argv) > 1 and sys.argv[1].strip():  # 로컬 테스트용
        raw = sys.argv[1].strip()
    return raw


def _report(conn) -> dict:
    import cpk_market_report as mr
    return mr.report(conn)


def main() -> int:
    kw = _keyword()
    if not kw or not ALLOWED.match(kw):
        print(json.dumps({"markets": [], "error": "badinput",
                          "message": "허용되지 않는 검색어예요."}, ensure_ascii=False))
        return 0
    try:
        import cpk_browser as cb
        import cpk_domeggook as dg
        import cpk_market_db as mdb
        import cpk_market_parallel as mp
        import cpk_session as cs
    except Exception as e:
        print(json.dumps({"markets": [], "error": f"import: {e}",
                          "message": "서버 준비 중 오류예요."}, ensure_ascii=False))
        return 0
    try:
        if not cb.chrome_alive():
            cb.launch_chrome()
        conn = mdb.connect(str(cs.HOME / "market.db"))
        today = time.strftime("%Y-%m-%d")
        r = mp.collect_parallel(conn, [kw], lambda k: dg.check_existence(k), today, workers=1)
        rep = _report(conn)
        rep["collected"] = r  # 이번 수집 요약(대시보드가 참고 가능)
        print(json.dumps(rep, ensure_ascii=False))
    except Exception as e:  # 실패해도 파싱 가능한 JSON
        print(json.dumps({"markets": [], "error": f"{type(e).__name__}: {e}",
                          "message": "수집 중 오류가 났어요. 잠시 후 다시 시도해 주세요."},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
