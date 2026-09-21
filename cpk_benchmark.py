"""Bounded, opt-in comparisons with the same required search-data contract."""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
from pathlib import Path

__all__ = ["contract", "summarize"]
BACKENDS = ("browser_fresh", "browser_reuse", "direct_browser", "alternate_proxy", "zyte")


def contract(result: dict) -> dict:
    """Do not count fast errors or missing keyword data as a successful search."""
    valid_search = result.get("outcome") in ("ok", "no_results")
    items = result.get("items") or []
    fields = ("name", "price", "reviews", "badge", "ad", "product_id")
    products_valid = all(all(field in item for field in fields) for item in items)
    if result.get("outcome") == "ok" and not items:
        products_valid = False
    passed = (
        valid_search and products_valid and result.get("related_ok") is True and result.get("autocomplete_ok") is True
    )
    return {
        "passed": passed,
        "products_valid": products_valid,
        "count": len(items),
        "related_ok": result.get("related_ok"),
        "autocomplete_ok": result.get("autocomplete_ok"),
        "related_count": len(result.get("related_keywords") or []),
        "autocomplete_count": len(result.get("autocomplete") or []),
        "product_order": [item.get("product_id") for item in items],
    }


def summarize(rows: list[dict]) -> dict:
    """Report all attempts; keep p95 unset until at least 100 successful samples exist."""
    valid = [row for row in rows if row.get("contract", {}).get("passed")]
    durations = sorted(row["timing"]["elapsed_ms"] for row in valid)
    essentials = sorted(
        row["timing"]["milestones"]["essential_ready_ms"]
        for row in valid
        if "essential_ready_ms" in row["timing"].get("milestones", {})
    )
    return {
        "attempts": len(rows),
        "successes": len(valid),
        "success_rate": len(valid) / len(rows) if rows else None,
        "total_p50_ms": statistics.median(durations) if durations else None,
        "essential_p50_ms": statistics.median(essentials) if essentials else None,
        "essential_p95_ms": essentials[int((len(essentials) - 1) * 0.95)] if len(essentials) >= 100 else None,
        "p95_note": "100 successful samples required; five-keyword trials are diagnostic only",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=BACKENDS, default="browser_reuse")
    parser.add_argument("--queries-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="Inspect configuration only; no network requests")
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.summarize:
        rows = [json.loads(line) for line in args.summarize.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(json.dumps(summarize(rows), ensure_ascii=False))
        return
    available = {
        "browser_fresh": True,
        "browser_reuse": True,
        "direct_browser": True,
        "alternate_proxy": bool(os.environ.get("CPK_ALT_PROXY", "")),
        "zyte": bool(os.environ.get("CPK_ZYTE_API_KEY", "")),
    }
    if args.check or not available[args.backend]:
        print(json.dumps({"configured": available, "selected": args.backend, "network_requests": 0}))
        return
    if not args.queries_file or not args.output or not 1 <= args.limit <= 20:
        parser.error("--queries-file, --output and a --limit between 1 and 20 are required")
    # Set provider-specific configuration before importing any browser module.
    if args.backend == "direct_browser":
        for name in ("CPK_PROXY", "CPK_PROXY_USER", "CPK_PROXY_PASS"):
            os.environ.pop(name, None)
    if args.backend == "alternate_proxy":
        os.environ["CPK_PROXY"] = os.environ["CPK_ALT_PROXY"]
        os.environ["CPK_PROXY_USER"] = os.environ.get("CPK_ALT_PROXY_USER", "")
        os.environ["CPK_PROXY_PASS"] = os.environ.get("CPK_ALT_PROXY_PASS", "")
        os.environ["CPK_PROXY_STICKY"] = "0"  # supply this provider's sticky credentials explicitly
        os.environ["CPK_PROXY_CDP_PORT"] = os.environ.get("CPK_ALT_PROXY_CDP_PORT", "9224")
        os.environ["CPK_PROXY_PROFILE"] = "chrome-profile-benchmark-alt"
    import cpk_engine
    import cpk_session as cs
    from cpk_metrics import Trace
    from cpk_worker import run_job, valid_keyword

    queries = list(
        dict.fromkeys(
            q.strip()
            for q in args.queries_file.read_text(encoding="utf-8").splitlines()
            if q.strip() and not q.lstrip().startswith("#")
        )
    )
    if not queries or any(not valid_keyword(q) for q in queries):
        parser.error("queries must contain valid, nonempty keywords")
    random.Random(args.seed).shuffle(queries)
    if args.backend == "zyte":
        from cpk_providers import ZyteSearch

        browser = ZyteSearch()
    else:
        browser = cpk_engine.BrowserSearch(reuse=args.backend != "browser_fresh")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for query in queries[: args.limit]:
            if cs.paused_remaining() or cs.today_count() >= cs.DAILY_BUDGET:
                print(json.dumps({"stopped": "paused_or_budget"}), flush=True)
                break
            trace = Trace(timeout=args.timeout)

            def publish(section, status, data, trace=trace):
                if section in ("search", "autocomplete") and status in ("ok", "no_results"):
                    trace.mark("first_result_ms")

            try:
                response = run_job(query, trace, browser, publish, db_path=args.output.with_suffix(".sqlite"))
                result = response["result"]
            except Exception as exc:
                result = {"outcome": "timeout" if isinstance(exc, TimeoutError) else "load_error"}
            row = {
                "backend": args.backend,
                "query": query,
                "outcome": result.get("outcome"),
                "session_reused": result.get("session_reused"),
                "attempts": result.get("attempts"),
                "contract": contract(result),
                "timing": trace.snapshot(),
            }
            rows.append(row)
            with args.output.open("a", encoding="utf-8") as output:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if result.get("outcome") not in ("ok", "no_results"):
                print(json.dumps({"stopped": "blocked"}), flush=True)
                break
            # Preserve the context and service pending events while pacing trials.
            until = time.monotonic() + 5
            while time.monotonic() < until:
                browser.idle()
                time.sleep(0.1)
    finally:
        browser.close()
    print(json.dumps({"summary": summarize(rows)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
