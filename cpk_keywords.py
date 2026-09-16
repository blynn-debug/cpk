"""검색어 하나로 연관검색어 + 자동완성만 뽑는다. 검색어 확장용.

연관검색어는 검색 결과 페이지에서 오므로 브라우저 경로(cb.search)를 쓴다(구형 HTTP 경로는 자주 막힘).
자동완성은 공용 헬퍼(cs.autocomplete_keywords)로 검색·수집과 경로를 통일한다.

사용: python cpk_keywords.py "검색어" [--json] [--depth 1]
  --depth 2 이면 1차 결과 검색어 각각에 대해 자동완성을 한 번 더 호출한다(요청 수 주의).
"""
from __future__ import annotations

import argparse
import json
import sys

import cpk_browser as cb
import cpk_session as cs


def keywords(query: str) -> dict:
    r = cb.search(query, with_autocomplete=True)
    return {"query": query, "outcome": r["outcome"], "http_status": r["http_status"],
            "related_keywords": r["related_keywords"],
            "autocomplete": r["autocomplete"], "autocomplete_query": r["autocomplete_query"],
            "autocomplete_ok": r["autocomplete_ok"], "total_count": r["total_count"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--depth", type=int, default=1)
    a = ap.parse_args()
    res = keywords(a.query)
    if a.depth >= 2:
        res["expanded"] = {}
        for kw in dict.fromkeys(res["related_keywords"] + res["autocomplete"]):
            res["expanded"][kw] = cs.autocomplete_keywords(kw)["items"]
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        tc = f" total={res['total_count']:,}" if res.get("total_count") else ""
        print(f"[{res['query']}] outcome={res['outcome']}{tc}")
        print("연관검색어: " + " | ".join(res["related_keywords"]))
        tag = "" if res["autocomplete_query"] == res["query"] else f" (축약 '{res['autocomplete_query']}')"
        if res["autocomplete_ok"] is False:
            print("자동완성: (조회 실패)")
        else:
            print(f"자동완성{tag}: " + " | ".join(res["autocomplete"]))
        for kw, sub in res.get("expanded", {}).items():
            print(f"  {kw} -> " + " | ".join(sub))
    return 0 if res["outcome"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
