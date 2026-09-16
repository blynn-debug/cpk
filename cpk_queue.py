"""검색어 작업 큐 + 재개. 대량 검색어를 영속 큐로 관리하고, 중단·차단·예산으로 멈춰도
다음 실행에서 미완료(pending) 항목부터 이어서 처리한다.

상태: ~/cpk/state/queue.json  { job_id, created, items:[{query,page,status,outcome,http_status,ts,attempts}] }
  status: pending(대기) / ok / no_results / failed
데이터: cpk_collect.save_result 를 그대로 써서 data/ 에 저장(runs.jsonl 포함).

사용:
  python cpk_queue.py add [keywords.txt]     # 파일(기본 keywords.txt)의 검색어를 큐에 추가
  python cpk_queue.py run [--limit N]         # pending 부터 처리(중단되면 멈추고 pending 유지)
  python cpk_queue.py status                   # 진행률·남은 예산·마지막 결과
  python cpk_queue.py reset                     # 큐 비우기
재개는 run 을 다시 부르면 된다(pending 부터 자동 이어서).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import random
import uuid

import cpk_session as cs
import cpk_collect as cc
import cpk_search as sr


def _qpath():
    return cs.HOME / "queue.json"


def _load() -> dict:
    p = _qpath()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(q: dict) -> None:
    cs.atomic_write(_qpath(), json.dumps(q, ensure_ascii=False, indent=1))


def enqueue(items: list) -> dict:
    """items: [(query, pages)]. 큐가 없으면 새로 만들고, 이미 있으면 새 검색어만 pending 으로 추가한다."""
    with cs._flock("queue.lock", timeout=30, blocking=True):
        q = _load()
        if not q.get("items"):
            q = {"job_id": time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6],
                 "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "items": []}
        have = {(it["query"], it["page"]) for it in q["items"]}
        added = 0
        for query, pages in items:
            for page in range(1, pages + 1):
                if (query, page) not in have:
                    q["items"].append({"query": query, "page": page, "status": "pending",
                                       "outcome": None, "http_status": None, "ts": None, "attempts": 0})
                    added += 1
        _save(q)
        return {"job_id": q["job_id"], "added": added, "total": len(q["items"])}


def _counts(q: dict) -> dict:
    c = {"pending": 0, "ok": 0, "no_results": 0, "failed": 0}
    for it in q.get("items", []):
        c[it["status"]] = c.get(it["status"], 0) + 1
    return c


def run_queue(limit: int | None = None) -> int:
    """pending 항목을 순회 처리한다. 중단·예산·차단이면 멈추고 해당 항목은 pending 으로 남긴다(재개 대상).
    ok/no_results 는 완료, load_error 는 failed 로 기록하고 다음으로 넘어간다."""
    import cpk_browser as cb
    if not cb.available():
        cs.log("queue: Chrome 없음")
        return 2
    # 동시 실행 방지(수집기와 같은 잠금 공유).
    lock = cs._flock("collect.run.lock", blocking=False)
    if not lock.__enter__():
        cs.log("queue: 이미 수집/큐 실행 중 — 건너뜀")
        return 9
    try:
        with cs._flock("queue.lock", timeout=30, blocking=True):
            q = _load()
        if not q.get("items"):
            cs.log("queue: 큐가 비어 있음")
            return 6
        run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        pending = [it for it in q["items"] if it["status"] == "pending"]
        if limit:
            pending = pending[:limit]
        cs.log(f"queue: 실행 {run_id} — pending {len(pending)}건")
        done_n = fail_n = 0
        stopped = None
        first = True
        with cb.search_session("collect") as fetch:
            if fetch is None:
                cs.log("queue: 브라우저 세션 못 얻음 — 건너뜀")
                return 8
            for it in pending:
                if not first:
                    time.sleep(random.uniform(cc.GAP_MIN, cc.GAP_MAX))
                first = False
                it["attempts"] += 1
                r = fetch(it["query"], it["page"])
                oc = r["outcome"]
                if oc in ("paused", "budget"):
                    cs.log(f"queue: {oc} — 중단(남은 pending 재개 대상)")
                    stopped = oc
                    break
                if oc in ("ok", "no_results"):
                    html = r["html"]
                    rows = sr.parse(html) if oc == "ok" else []
                    counts: dict = {}
                    for row in rows:
                        counts[row["badge"]] = counts.get(row["badge"], 0) + 1
                    cc.save_result({"query": it["query"], "page": it["page"], "run_id": run_id,
                                    "outcome": oc, "http_status": r["http_status"], "count": len(rows),
                                    "total_count": sr.parse_total_count(html) if oc == "ok" else 0,
                                    "badge_counts": counts, "ads": sum(x["ad"] for x in rows),
                                    "related_keywords": sr.parse_related_keywords(html) if oc == "ok" else [],
                                    "autocomplete": cs.autocomplete_keywords(it["query"])["items"] if oc == "ok" else [],
                                    "items": rows})
                    it["status"] = oc
                    done_n += 1
                elif oc in ("challenge", "http_error"):
                    # 명확한 차단은 검색 경로가 공통 중단을 이미 걸었다. 이 항목은 pending 유지(재개).
                    cs.log(f"queue: {it['query']} p{it['page']} {oc} — 중단")
                    stopped = oc
                    break
                else:  # load_error 등 일시 오류: 실패로 기록하고 다음 진행
                    it["status"] = "failed"
                    fail_n += 1
                it["outcome"] = oc
                it["http_status"] = r.get("http_status")
                it["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                with cs._flock("queue.lock", timeout=30, blocking=True):
                    _save(q)   # 매 항목 원자적 저장(중단·크래시에도 진행 보존)
        with cs._flock("queue.lock", timeout=30, blocking=True):
            _save(q)
        c = _counts(q)
        cs.log(f"queue 종료({run_id}): done={done_n} fail={fail_n} stopped={stopped} "
               f"남은 pending={c['pending']}")
        if not cc.SYNC_TARGET:
            pass
        else:
            cc.sync()
        return 0
    finally:
        lock.__exit__(None, None, None)


def status() -> dict:
    q = _load()
    c = _counts(q)
    total = len(q.get("items", []))
    return {"job_id": q.get("job_id"), "total": total, **c,
            "done_pct": round(100 * (total - c["pending"]) / total) if total else 0,
            "budget_used_today": cs.today_count(), "budget": cs.DAILY_BUDGET,
            "paused_remaining_sec": round(cs.paused_remaining())}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("file", nargs="?", default=str(cc.KEYWORDS))
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int)
    sub.add_parser("status")
    sub.add_parser("reset")
    args = ap.parse_args()

    if args.cmd == "add":
        cc.KEYWORDS = __import__("pathlib").Path(args.file)
        kws = cc.load_keywords()
        if not kws:
            print(f"검색어 없음: {args.file}", file=sys.stderr)
            return 6
        print(json.dumps(enqueue(kws), ensure_ascii=False))
        return 0
    if args.cmd == "run":
        return run_queue(limit=args.limit)
    if args.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "reset":
        with cs._flock("queue.lock", timeout=30, blocking=True):
            _save({})
        print("큐 초기화됨")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
