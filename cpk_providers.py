"""Optional managed-provider adapter; credentials stay in environment variables."""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request

import cpk_engine
import cpk_session as cs
from cpk_metrics import Trace

__all__ = ["ZyteSearch"]


class ZyteSearch:
    """Return the same search/autocomplete contract using Zyte's documented API.

    This is an opt-in benchmark adapter, not a claim of verified Coupang support.
    Docs: https://docs.zyte.com/zyte-api/usage/browser.html
    """

    def __init__(self, *, opener=urllib.request.urlopen):
        self.key = os.environ.get("CPK_ZYTE_API_KEY", "").strip()
        if not self.key:
            raise ValueError("CPK_ZYTE_API_KEY is not configured")
        self.opener = opener

    def _fetch(self, url: str, trace: Trace, *, browser: bool) -> dict:
        cs.admit_request("benchmark_zyte", limit=cs.DAILY_BUDGET, timeout=trace.remaining(5))
        payload = {"url": url, "geolocation": "KR", "browserHtml" if browser else "httpResponseBody": True}
        auth = base64.b64encode((self.key + ":").encode()).decode()
        request = urllib.request.Request(
            "https://api.zyte.com/v1/extract",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
        )
        with self.opener(request, timeout=trace.remaining(40)) as response:
            return json.load(response)

    def search(self, query: str, trace: Trace) -> dict:
        """Fetch rendered search HTML, then use the same parser and outcome checks."""
        url = cs.SEARCH_URL.format(q=urllib.parse.quote(query))
        with trace.stage("managed_search"):
            response = self._fetch(url, trace, browser=True)
        return cpk_engine.parse_search(
            query, response.get("browserHtml", ""), response.get("statusCode"), final_url=response.get("url", url)
        )

    def autocomplete(self, query: str, trace: Trace) -> dict:
        """Fetch autocomplete JSON separately and preserve failed-versus-empty status."""
        try:
            url = cs.AUTOCOMPLETE_URL.format(q=urllib.parse.quote(query), ts=0)
            with trace.stage("managed_autocomplete"):
                response = self._fetch(url, trace, browser=False)
                data = json.loads(base64.b64decode(response.get("httpResponseBody", "")))
            ok = response.get("statusCode") == 200 and isinstance(data, list)
            items = [item["keyword"] for item in data if isinstance(item, dict) and item.get("keyword")] if ok else []
            return {"ok": ok, "items": items, "used": query, "outcome": "ok" if ok else "http_error"}
        except Exception:
            return {"ok": False, "items": [], "used": query, "outcome": "load_error"}

    def close(self) -> None:
        """No persistent browser is owned by this adapter."""

    def idle(self) -> None:
        """No keepalive or background request."""
