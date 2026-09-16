"""Offline diagnosis of the 2026-09-17 live failure; NOT a live probe.

Run on macOS/Linux:
  python tests/audit_live_failure_20260917.py --evidence /path/to/failure.json

Replays CDP message ordering through the real Tab.call/goto/eval methods.
Uses temporary control state and fake transport; never opens Chrome or HTTP.
Exit 1 means at least one expected guarantee is still violated, not a fix.
This audit intentionally stays outside unittest's test_* discovery.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest import mock
import urllib.request


class Wire:
    def __init__(self, messages=(), responder=None):
        self.messages = list(messages)
        self.responder = responder
        self.received = []

    def send(self, body):
        if self.responder:
            self.messages.extend(self.responder(json.loads(body)))

    def recv(self):
        if not self.messages:
            raise TimeoutError("Synthetic transport timeout")
        message = self.messages.pop(0)
        self.received.append(message)
        return json.dumps(message)

    def settimeout(self, seconds):
        pass


def response(status=403, loader="current"):
    return {"method": "Network.responseReceived", "params": {
        "type": "Document", "frameId": "main", "loaderId": loader,
        "requestId": "document-1", "response": {"status": status}}}


def ack(identifier=1, **extra):
    return {"id": identifier, "result": {
        "frameId": "main", "loaderId": "current", **extra}}


def loaded():
    return {"method": "Page.loadEventFired", "params": {"timestamp": 1}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", type=Path, required=True)
    args = ap.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    html = evidence["html_head"]
    # Require the actual denial artifact, not an arbitrary zero-product fixture.
    if 'id="error403"' not in html or "사용권한이 없습니다" not in html:
        raise SystemExit("Expected the saved Coupang access-denied page")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    observations = []

    def check(name, actual, expected):
        record = {"case": name, "actual": actual, "expected": expected,
                  "passed": all(actual.get(k) == v for k, v in expected.items())}
        observations.append(record)
        print(json.dumps(record, ensure_ascii=True), flush=True)

    with tempfile.TemporaryDirectory(prefix="cpk_live_failure_audit_") as scratch:
        with mock.patch.dict(os.environ, CPK_HOME=scratch, CPK_SEARCH_MIN_GAP="0",
                             CPK_DAILY_BUDGET="100", CPK_BACKOFF_HOURS="6",
                             CPK_NTFY_TOPIC="", CPK_SYNC_TARGET=""):
            import cpk_browser as cb
            import cpk_search as sr
            import cpk_session as cs

            with contextlib.ExitStack() as patches:
                guards = []
                for obj, attr in [(socket.socket, "connect"),
                                  (urllib.request, "urlopen"),
                                  (cs.requests.Session, "request"),
                                  (cb, "_http"), (cb, "launch_chrome"),
                                  (cb, "quit_chrome")]:
                    guards.append(patches.enter_context(mock.patch.object(
                        obj, attr, side_effect=AssertionError("Live access forbidden"))))
                patches.enter_context(mock.patch.object(cb.time, "sleep"))

                def tab(messages):
                    t = cb.Tab.__new__(cb.Tab)
                    t.seq, t.last_status, t.last_error = 0, None, None
                    t.ws = Wire(messages)
                    return t

                t = tab([ack(), response(), loaded()])
                t.goto("https://example.invalid/search", settle=(0, 0))
                check("control_response_after_ack", {"status": t.last_status},
                      {"status": 403})

                t = tab([response(), ack(), loaded()])
                t.goto("https://example.invalid/search", settle=(0, 0))
                check("response_before_ack_is_retained", {"status": t.last_status},
                      {"status": 403})

                t = tab([ack(), loaded(), response(),
                         {"id": 2, "result": {"result": {"value": html}}}])
                t.goto("https://example.invalid/search", settle=(0, 0))
                pending_after_goto = len(t.ws.messages)
                observed_html = t.eval("document.documentElement.outerHTML")
                check("late_response_during_eval_is_retained",
                      {"status": t.last_status, "pending_after_goto": pending_after_goto,
                       "html_received": observed_html == html},
                      {"status": 403, "html_received": True})

                t = tab([ack(errorText="net::ERR_NAME_NOT_RESOLVED"), loaded()])
                t.goto("https://example.invalid/search", settle=(0, 0))
                check("navigate_error_is_retained", {"error": t.last_error},
                      {"error": "net::ERR_NAME_NOT_RESOLVED"})

                t = tab([ack()])
                t.goto("https://example.invalid/search", settle=(0, 0))
                check("transport_timeout_is_explained",
                      {"has_error": bool(t.last_error)}, {"has_error": True})

                check("control_denied_page_has_no_products",
                      {"parsed_products": len(sr.parse(html))}, {"parsed_products": 0})

                outcome = cb.classify_outcome(None, html, 0)
                paused = cs.maybe_pause_on_block(outcome, None, reason="offline denial")
                check("saved_denial_without_status_stops_requests",
                      {"outcome": outcome, "pause_applied": paused,
                       "paused": cs.paused_remaining() > 0},
                      {"pause_applied": True, "paused": True})
                cs.clear_pause()

                # Exercise the standard on-demand path, including its warmup,
                # retry and autocomplete policy. The real CDP event reader is
                # still used; only its wire and tab creation are replaced.
                current_url = [""]

                def respond(command):
                    method, identifier = command["method"], command["id"]
                    if method == "Page.navigate":
                        current_url[0] = command["params"]["url"]
                        status = 200 if current_url[0] == cs.HOME_URL else 403
                        return [response(status), ack(identifier), loaded()]
                    if method == "Runtime.evaluate":
                        expr = command["params"]["expression"]
                        if "outerHTML" in expr:
                            value = html
                        elif "ProductUnit" in expr:
                            value = 0
                        elif "location" in expr:
                            value = current_url[0]
                        else:
                            value = "쿠팡!"
                        return [{"id": identifier, "result": {"result": {"value": value}}}]
                    raise AssertionError(f"Unexpected fake CDP command: {method}")

                class ReplayedTab(cb.Tab):
                    def __init__(self):
                        self.seq, self.last_status, self.last_error = 0, None, None
                        self.ws = Wire(responder=respond)

                    def close(self):
                        pass

                autocomplete_calls = []

                def fake_autocomplete(session, query, referer=None):
                    autocomplete_calls.append(query)
                    return {"items": [], "status": 200, "ok": True}

                with mock.patch.object(cb, "Tab", ReplayedTab), \
                        mock.patch.object(cb, "chrome_alive", return_value=True), \
                        mock.patch.object(cs, "make_session", return_value=object()), \
                        mock.patch.object(cs, "autocomplete", side_effect=fake_autocomplete):
                    result = cb.search(evidence["query"])
                check("standard_search_halts_after_recognizable_denial",
                      {"outcome": result["outcome"], "paused": cs.paused_remaining() > 0,
                       "autocomplete_calls": len(autocomplete_calls),
                       "counts": cs.read_control().get("counts", {})},
                      {"paused": True, "autocomplete_calls": 0})

                for guard in guards:
                    guard.assert_not_called()

    passed = sum(row["passed"] for row in observations)
    print(json.dumps({"checks": len(observations), "passed": passed,
                      "violations": len(observations)-passed, "live_requests": 0}))
    return 0 if passed == len(observations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
