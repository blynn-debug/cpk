"""Measure the existing market path without changing its navigation behavior.

Run on the Mac mini with production environment loaded. Output contains counts,
outcomes and durations only. Home/autocomplete requests missing from the old
budget accounting are admitted explicitly. This never clears a shared pause.
"""

from __future__ import annotations

import argparse
import functools
import json
import random
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("queries", nargs="+")
    args = parser.parse_args()
    sys.path.insert(0, str(args.root))
    import cpk_browser as cb
    import cpk_domeggook as dg
    import cpk_market_parallel as mp
    import cpk_session as cs

    stages = []

    def timed(name, fn, admission=None):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if admission:
                cs.admit_request(admission, limit=cs.DAILY_BUDGET)
            started = time.monotonic()
            try:
                return fn(*a, **kw)
            finally:
                stages.append({"stage": name, "duration_ms": round((time.monotonic() - started) * 1000, 1)})

        return wrapper

    original_goto = cb.Tab.goto

    def goto(tab, url, *a, **kw):
        home = url == cs.HOME_URL
        return timed("home" if home else "search", original_goto, "benchmark_home" if home else None)(
            tab, url, *a, **kw
        )

    cb.Tab.goto = goto
    cb.open_incognito_tab = timed("context", cb.open_incognito_tab)
    cb.warm_context = timed("warmup", cb.warm_context)
    cs.autocomplete = timed("autocomplete", cs.autocomplete, "benchmark_autocomplete")
    dg.check_existence = timed("sourcing", dg.check_existence)
    queries = list(dict.fromkeys(query.strip() for query in args.queries if query.strip()))
    random.Random(20260922).shuffle(queries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for query in queries:
        if cs.paused_remaining() or cs.today_count() >= cs.DAILY_BUDGET:
            print(json.dumps({"stopped": "paused_or_budget"}), flush=True)
            break
        stages.clear()
        started = time.monotonic()
        result = mp.fetch_one(query)
        source_ok = False
        if result.get("outcome") == "ok":
            try:
                dg.check_existence(query)
                source_ok = True
            except Exception:
                pass
        row = {
            "backend": "baseline",
            "query": query,
            "outcome": result.get("outcome"),
            "count": result.get("count", 0),
            "related_count": len(result.get("related_keywords", [])),
            "autocomplete_ok": result.get("autocomplete_ok"),
            "autocomplete_count": len(result.get("autocomplete", [])),
            "sourcing_ok": source_ok,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "stages": list(stages),
        }
        with args.output.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if result.get("outcome") in ("challenge", "http_error", "paused", "budget"):
            print(json.dumps({"stopped": "blocked_baseline"}), flush=True)
            break
        time.sleep(5)


if __name__ == "__main__":
    main()
