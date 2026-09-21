"""A browser search session owned by one worker thread, reusable across requests."""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.parse

import cpk_browser as cb
import cpk_search as sr
import cpk_session as cs
from cpk_metrics import Trace

__all__ = ["BrowserSearch", "autocomplete", "parse_search"]


def parse_search(query: str, html: str, status: int | None, *, final_url: str = "") -> dict:
    """Preserve the existing data contract and reject mismatched or incomplete pages."""
    rows = sr.parse(html)
    outcome = cb.classify_outcome(status, html, len(rows))
    if cs.is_challenge(html) or (status is not None and status >= 400):
        outcome = "challenge" if cs.is_challenge(html) else "http_error"
    if outcome == "no_results" and not any(
        marker in html for marker in ("검색결과가 없습니다", "검색 결과가 없습니다", "검색된 상품이 없습니다")
    ):
        outcome = "load_error"
    if final_url and outcome in ("ok", "no_results"):
        actual = urllib.parse.parse_qs(urllib.parse.urlsplit(final_url).query).get("q", [None])[0]
        if actual != query:
            outcome = "load_error"
    if outcome != "ok":
        rows = []
    badges: dict[str, int] = {}
    for row in rows:
        badges[row["badge"]] = badges.get(row["badge"], 0) + 1
    return {
        "query": query,
        "page": 1,
        "outcome": outcome,
        "http_status": status,
        "count": len(rows),
        "total_count": sr.parse_total_count(html) if outcome == "ok" else None,
        "badge_counts": badges,
        "ads": sum(bool(row["ad"]) for row in rows),
        "related_keywords": sr.parse_related_keywords(html) if outcome == "ok" else [],
        "related_ok": outcome in ("ok", "no_results"),
        "items": rows,
    }


def _admit(kind: str, trace: Trace) -> None:
    with trace.stage("admission"):
        cs.admit_request(kind, limit=cs.DAILY_BUDGET, timeout=trace.remaining(5))


def autocomplete(query: str, trace: Trace) -> dict:
    """Fetch autocomplete independently, accounting for budget and without shared cookie writes."""
    try:
        _admit("worker_autocomplete", trace)
        with trace.stage("autocomplete"):
            with cs.make_session() as session:
                result = cs.autocomplete(session, query, timeout=trace.remaining(20), persist_cookies=False)
        if result.get("status") == 429 or (not cb.PROXY and result.get("status") == 403):
            cs.maybe_pause_on_block("http_error", result["status"], reason="worker autocomplete")
        return {
            "ok": result["ok"],
            "items": [item["keyword"] for item in result["items"]],
            "used": query,
            "outcome": "ok" if result["ok"] else "http_error",
        }
    except cs.RequestPaused:
        return {"ok": False, "items": [], "used": query, "outcome": "paused"}
    except cs.BudgetExceeded:
        return {"ok": False, "items": [], "used": query, "outcome": "budget"}
    except Exception as exc:
        return {
            "ok": False,
            "items": [],
            "used": query,
            "outcome": "timeout" if isinstance(exc, TimeoutError) else "load_error",
        }


class BrowserSearch:
    """Keep a context until its TTL expires or navigation fails. All calls use one thread."""

    def __init__(self, *, reuse: bool = True, ttl: float | None = None):
        self.reuse = reuse
        self.backend = "proxy_browser" if cb.PROXY else "direct_browser"
        self.ttl = ttl if ttl is not None else min(float(os.environ.get("CPK_CONTEXT_TTL", "480")), cb.PROXY_TTL * 60)
        self.tab = None
        self.closer = None
        self.created = 0.0

    def close(self) -> None:
        """Release this context only; never terminate another user's Chrome."""
        closer, self.closer = self.closer, None
        self.tab = None
        if closer:
            try:
                closer()
            except Exception:
                pass

    def idle(self) -> None:
        """Service already-pending browser events without navigating or sending keepalive requests."""
        if self.tab is None:
            return
        if time.monotonic() - self.created >= self.ttl:
            self.close()
            return
        try:
            self.tab.deadline = None
            self.tab.pump(0.05)
        except Exception:
            self.close()

    @contextlib.contextmanager
    def navigation(self, kind: str, trace: Trace):
        """Share browser-navigation spacing with legacy callers and pump pending events."""
        with trace.stage("navigation_queue"):
            gate = cs.search_gate(timeout=trace.remaining(), wait=self.tab.pump if self.tab else None)
            gate.__enter__()
        try:
            _admit(kind, trace)
            yield
        finally:
            gate.__exit__(None, None, None)

    def search(self, query: str, trace: Trace, *, tries: int = 2) -> dict:
        """Search with a shared deadline and a fresh context only when needed."""
        result = parse_search(query, "", None)
        for attempt in range(1, max(1, tries) + 1):
            try:
                trace.remaining()
                if self.tab is not None and time.monotonic() - self.created >= self.ttl:
                    self.close()
                reused = self.tab is not None
                if self.tab is None:
                    # Check pause/budget before creating browser resources; count at navigation admission.
                    if cs.paused_remaining():
                        raise cs.RequestPaused(cs.paused_remaining())
                    if cs.today_count() >= cs.DAILY_BUDGET:
                        raise cs.BudgetExceeded("daily budget reached")
                    with trace.stage("context", attempt=attempt):
                        if not cb.chrome_alive(timeout=trace.remaining(2)):
                            cb.launch_chrome(deadline=trace.deadline)
                        self.tab, self.closer = cb.open_incognito_tab(cb.PROXY, deadline=trace.deadline)
                        self.created = time.monotonic()
                    self.tab.deadline = trace.deadline
                    with self.navigation("worker_home", trace):
                        with trace.stage("home", attempt=attempt):
                            self.tab.goto(cs.HOME_URL, settle=(0, 0), timeout=trace.remaining(40))
                        trace.observe(
                            "home",
                            attempt=attempt,
                            http_status=self.tab.last_status,
                            navigation_error=bool(getattr(self.tab, "last_error", None)),
                            network_ms=getattr(self.tab, "last_network", {}),
                        )
                    with trace.stage("warmup", attempt=attempt):
                        if not cb.warm_context(self.tab, max_wait=trace.remaining(cb.WARM_SECS)):
                            raise RuntimeError("home search input unavailable")
                self.tab.deadline = trace.deadline
                url = cs.SEARCH_URL.format(q=urllib.parse.quote(query))
                with self.navigation("worker_search", trace):
                    with trace.stage("search", attempt=attempt):
                        self.tab.goto(url, settle=(0, 0), timeout=trace.remaining(25), ready_query=query)
                        snapshot = self.tab.eval(
                            "JSON.stringify({html:document.documentElement.outerHTML,url:location.href})"
                        )
                        data = json.loads(snapshot or "{}")
                with trace.stage("parse", attempt=attempt):
                    result = parse_search(
                        query, data.get("html", ""), self.tab.last_status, final_url=data.get("url", "")
                    )
                    if result["outcome"] in ("ok", "no_results") and not self.tab.last_ready:
                        result = parse_search(query, "", None)
                result.update(attempts=attempt, session_reused=reused)
                trace.observe(
                    "search",
                    attempt=attempt,
                    outcome=result["outcome"],
                    http_status=self.tab.last_status,
                    ready=self.tab.last_ready,
                    navigation_error=bool(getattr(self.tab, "last_error", None)),
                    network_ms=getattr(self.tab, "last_network", {}),
                )
                if result["outcome"] in ("ok", "no_results"):
                    if not self.reuse:
                        self.close()
                    return result
                self.close()
                # Protect the home IP: direct-route challenge/403 stops without rotation or retry.
                if result.get("http_status") == 429 or (
                    not cb.PROXY and (result["outcome"] == "challenge" or result.get("http_status") == 403)
                ):
                    cs.maybe_pause_on_block(result["outcome"], result.get("http_status"), reason="worker blocked")
                    break
            except (cs.RequestPaused, cs.BudgetExceeded) as exc:
                result["outcome"] = "paused" if isinstance(exc, cs.RequestPaused) else "budget"
                self.close()
                break
            except Exception as exc:
                self.close()
                result["outcome"] = "timeout" if isinstance(exc, TimeoutError) else "load_error"
                result["attempts"] = attempt
                if isinstance(exc, TimeoutError):
                    break
        return result
