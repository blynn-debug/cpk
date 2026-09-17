"""맥미니에서 키워드 1건을 검색해 JSON 한 줄을 stdout으로 낸다(웹/SSH 호출용).

키워드 출처(우선순위):
  1) 명령행 인자:  python cpk_search_json.py "계란보관함"
  2) 환경변수 SSH_ORIGINAL_COMMAND: authorized_keys forced-command 로 호출될 때
     (ssh <key> mini-remote '계란보관함' → forced-command 가 이 스크립트를 실행하고
      SSH_ORIGINAL_COMMAND 에 '계란보관함' 이 담긴다)

공용 요청 관문(cb.search → 간격·일일예산·차단 시 중단)을 그대로 통과하므로 웹에서 눌러도 안전하다.
성공/차단/대기/오류를 outcome 으로 구분해 낸다. 항상 JSON 한 줄만 출력한다(호출측 파싱 안정).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

MAX_ITEMS = 60
ALLOWED = re.compile(r"^[\w가-힣ㄱ-ㅎㅏ-ㅣ0-9 ().,+&/-]{1,60}$")


def _keyword() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    return (os.environ.get("SSH_ORIGINAL_COMMAND", "") or "").strip()


def _fail(msg: str, outcome: str = "error") -> int:
    print(json.dumps({"query": "", "outcome": outcome, "error": msg,
                      "count": 0, "items": []}, ensure_ascii=False))
    return 0  # 파싱 가능한 JSON을 냈으니 종료코드는 0


def main() -> int:
    q = _keyword()
    if not q:
        return _fail("빈 검색어", "error")
    if not ALLOWED.match(q):
        return _fail("허용되지 않는 문자", "error")

    import cpk_browser as cb
    t0 = time.time()
    try:
        r = cb.search(q)
    except Exception as e:  # 크롤러 예외도 JSON으로 포장
        return _fail(f"{type(e).__name__}: {e}", "error")

    items = []
    for row in (r.get("items") or [])[:MAX_ITEMS]:
        items.append({k: row.get(k) for k in
                      ("name", "price", "fee", "reviews", "badge", "ad", "product_id", "url")})
    out = {
        "query": r.get("query", q),
        "outcome": r.get("outcome"),
        "http_status": r.get("http_status"),
        "count": r.get("count", len(items)),
        "total_count": r.get("total_count"),
        "badge_counts": r.get("badge_counts", {}),
        "ads": r.get("ads"),
        "related_keywords": r.get("related_keywords", []),
        "autocomplete": r.get("autocomplete", []),
        "autocomplete_ok": r.get("autocomplete_ok"),
        "items": items,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "error": None,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
