"""마켓 레이더 병렬 수집: 여러 키워드를 각기 다른 프록시 출구 IP로 '동시에' 검색한다.

전역 검색 직렬화(cpk_session.search_gate = search.lock)는 우회한다 — 워커마다 프록시
세션(=출구 IP)이 달라 per-IP 요청률이 자연히 1스트림/IP 이므로 병렬이 밴 위험을 키우지
않는다. 대신 예산·중단(admit_request)과 차단 시 공통 중단(maybe_pause_on_block)은 유지한다.
open_incognito_tab 이 호출마다 독립 컨텍스트·독립 websocket 을 만들어 스레드 안전하다.
상시 Chrome(com.cpk.chrome, 9223)이 떠 있어야 한다.

정책(사용자 확정): 속도 우선·부분결과 허용 — 실패분은 스킵/1회 재시도, 10/10 보장 안 함.
"""
from __future__ import annotations

import concurrent.futures as cf
import urllib.parse


def _empty(query: str, outcome: str) -> dict:
    return {"query": query, "page": 1, "outcome": outcome, "http_status": None,
            "count": 0, "total_count": None, "badge_counts": {}, "ads": 0,
            "related_keywords": [], "autocomplete": [], "autocomplete_query": query,
            "autocomplete_ok": None, "items": []}


def _shape(query: str, last: dict, ac: dict) -> dict:
    """cb.search 와 동일한 형태의 결과 dict 로 만든다."""
    import cpk_search as sr
    html, outcome = last["html"], last["outcome"]
    rows = sr.parse(html) if outcome == "ok" else []
    counts: dict = {}
    for row in rows:
        counts[row["badge"]] = counts.get(row["badge"], 0) + 1
    return {"query": query, "page": 1, "outcome": outcome, "http_status": last["http_status"],
            "count": len(rows), "total_count": sr.parse_total_count(html) if outcome == "ok" else None,
            "badge_counts": counts, "ads": sum(r["ad"] for r in rows),
            "related_keywords": sr.parse_related_keywords(html) if outcome == "ok" else [],
            "autocomplete": ac["items"], "autocomplete_query": ac["used"], "autocomplete_ok": ac["ok"],
            "items": rows}


def fetch_one(query: str, tries: int = 2, warm: bool = True) -> dict:
    """검색 한 건 — 직렬화 없이 자기 프록시 컨텍스트(자기 출구 IP)로 수행한다.
    cb.search 와 같은 형태를 반환한다. 예산/중단이면 즉시 outcome=paused/budget 로 반환."""
    import cpk_browser as cb
    import cpk_session as cs
    url = cs.SEARCH_URL.format(q=urllib.parse.quote(query))
    last: dict | None = None
    for i in range(max(tries, 1)):
        try:
            cs.admit_request("market_par", limit=cs.DAILY_BUDGET)
        except cs.RequestPaused:
            return _empty(query, "paused")
        except cs.BudgetExceeded:
            return _empty(query, "budget")
        t, close = cb.open_incognito_tab(cb.PROXY)
        try:
            t.goto(cs.HOME_URL)
            if warm and cb.WARM_SECS > 0:
                cb.warm_context(t)
            t.goto(url, settle=(3.0, 5.0) if i == 0 else (5.0, 8.0))
            units = t.eval(cb.PRODUCT_JS) or 0
            html = t.eval("document.documentElement.outerHTML") or ""
            outcome = cb.classify_outcome(t.last_status, html, units)
            last = {"http_status": t.last_status, "outcome": outcome, "html": html}
        except Exception as e:  # 개별 워커 오류는 배치를 죽이지 않는다
            last = {"http_status": None, "outcome": "load_error", "html": "", "error": repr(e)}
        finally:
            close()
        if last["outcome"] in ("ok", "no_results"):
            break
    if last["outcome"] not in ("ok", "no_results"):
        cs.maybe_pause_on_block(last["outcome"], last["http_status"], reason="market_par")
    if last["outcome"] in ("paused", "budget"):
        ac = {"ok": None, "items": [], "used": query}
    else:
        ac = cs.autocomplete_keywords(query)
    return _shape(query, last, ac)


def search_many(queries, workers: int = 10, tries: int = 2, fetch_fn=fetch_one) -> dict:
    """여러 키워드를 동시에 검색한다. {keyword: result} 반환. 워커 수는 keyword 수로 상한."""
    uniq = [q for q in dict.fromkeys((k or "").strip() for k in queries) if q]
    out: dict = {}
    if not uniq:
        return out
    with cf.ThreadPoolExecutor(max_workers=min(workers, len(uniq))) as ex:
        futs = {ex.submit(fetch_fn, q, tries): q for q in uniq}
        for f in cf.as_completed(futs):
            q = futs[f]
            try:
                out[q] = f.result()
            except Exception:
                out[q] = _empty(q, "load_error")
    return out


def collect_parallel(conn, queries, sourcing_fn, today: str, *,
                     workers: int = 10, tries: int = 2, source: str = "parallel",
                     search_many_fn=search_many) -> dict:
    """병렬 검색 후 성공분만 DB에 저장(스냅샷·점수·소싱). DB 쓰기는 순차(sqlite 안전).
    반환: {collected, failed, detail:{kw: outcome}}."""
    import cpk_market as m
    import cpk_market_db as mdb
    import cpk_market_score as sc
    results = search_many_fn(queries, workers=workers, tries=tries)
    collected = failed = 0
    detail: dict = {}
    for kw, res in results.items():
        outcome = res.get("outcome")
        if outcome != "ok":
            failed += 1
            detail[kw] = outcome
            continue
        kid = mdb.upsert_keyword(conn, kw, source, status="tracked")
        mdb.insert_snapshot(conn, kid, m.snapshot_from_result(res), day=today)
        snaps = mdb.snapshots_for(conn, kid)
        mdb.save_scores(conn, kid, today, sc.opportunity(snaps))
        try:
            mdb.save_sourcing(conn, kid, sourcing_fn(kw))
        except Exception:
            pass  # 소싱 실패는 수집을 막지 않는다(다음 회차 재시도)
        collected += 1
        detail[kw] = "ok"
    return {"collected": collected, "failed": failed, "detail": detail}


def main() -> int:
    import json
    import sys
    import time
    import cpk_browser as cb
    import cpk_domeggook as dg
    import cpk_market_db as mdb
    import cpk_session as cs
    args = [a.strip() for a in sys.argv[1:] if a.strip()]
    if not args:
        print("usage: cpk_market_parallel.py <keyword> [keyword ...]", file=sys.stderr)
        return 2
    if not cb.chrome_alive():
        cb.launch_chrome()
        cs.log("parallel: Chrome 시작")
    conn = mdb.connect(str(cs.HOME / "market.db"))
    today = time.strftime("%Y-%m-%d")
    r = collect_parallel(conn, args, lambda kw: dg.check_existence(kw), today)
    cs.log(f"parallel 수집: {r}")
    print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
