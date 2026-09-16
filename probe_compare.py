"""url 직접 이동 vs ui 검색창 제출을 같은 시점에 관측 비교(진단 전용).
공통 관문·중단을 거치지 않고 저수준 Tab으로 결과만 관측한다(요청 제어에 영향 주지 않음).
사용: python probe_compare.py [url|ui|both]  (기본 both)
출력: 한 줄 JSON {mode: {units, http_status, outcome}}
"""
import json
import sys
import urllib.parse

import cpk_browser as cb
import cpk_session as cs

QUERY = "계란보관함"


def observe(mode: str) -> dict:
    tab = cb.Tab()
    try:
        if mode == "ui":
            tab.goto(cs.HOME_URL)
            how = tab.submit_search(QUERY)
        else:
            tab.goto(cs.SEARCH_URL.format(q=urllib.parse.quote(QUERY)))
            how = "navigate"
        units = tab.eval(cb.PRODUCT_JS) or 0
        html = tab.eval("document.documentElement.outerHTML") or ""
        outcome = cb.classify_outcome(tab.last_status, html, units)
        return {"mode": mode, "how": how, "units": units,
                "http_status": tab.last_status, "outcome": outcome}
    finally:
        tab.close()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    if not cb.chrome_alive():
        cb.launch_chrome()
    modes = ["url", "ui"] if mode == "both" else [mode]
    res = {}
    for m in modes:
        try:
            res[m] = observe(m)
        except Exception as e:
            res[m] = {"mode": m, "error": repr(e)}
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
