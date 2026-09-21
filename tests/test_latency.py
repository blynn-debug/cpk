"""Offline regression tests for deadlines, Fetch pumping, reuse and progressive jobs."""

import contextlib
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import cpk_browser as cb
import cpk_engine as engine
import cpk_session as cs
from cpk_metrics import DeadlineExceeded, Trace
from cpk_worker import JobManager, run_job

HTML = (Path(__file__).parent / "fixtures/search_sample.html").read_text(encoding="utf-8")


class Wire:
    """A CDP transport boundary that records commands and can deliver late events."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.timeouts = []

    def send(self, body):
        self.sent.append(json.loads(body))

    def recv(self):
        if not self.messages:
            time.sleep(0.001)
            raise TimeoutError("empty wire")
        return json.dumps(self.messages.pop(0))

    def settimeout(self, timeout):
        self.timeouts.append(timeout)


def tab_with(messages):
    tab = cb.Tab.__new__(cb.Tab)
    tab.seq = tab._fire_id = 0
    tab._proxy_auth = None
    tab._block_media = False
    tab.ws = Wire(messages)
    return tab


class BrowserEvents(unittest.TestCase):
    def test_warmup_keeps_processing_events_and_uses_page_readiness(self):
        from unittest.mock import Mock

        now = [0.0]
        tab = Mock()
        tab.eval.return_value = True
        tab.pump.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        with patch.object(cb.time, "monotonic", side_effect=lambda: now[0]):
            self.assertTrue(cb.warm_context(tab, max_wait=8))
        self.assertAlmostEqual(now[0], 8)
        self.assertGreater(tab.call.call_count, 0)
        self.assertIn("document.readyState", tab.eval.call_args.args[0])
        tab.cookies.assert_not_called()

    def test_late_fetch_is_released_during_settle(self):
        tab = tab_with(
            [
                {"id": 1, "result": {}},
                {"method": "Page.loadEventFired"},
                {"method": "Fetch.requestPaused", "params": {"requestId": "late", "resourceType": "XHR"}},
            ]
        )
        tab.goto("https://example.test", settle=(0.005, 0.005))
        self.assertTrue(any(message.get("method") == "Fetch.continueRequest" for message in tab.ws.sent))
        self.assertIsNone(tab.last_error)

    def test_load_before_navigation_ack_does_not_wait_again(self):
        tab = tab_with([{"method": "Page.loadEventFired"}, {"id": 1, "result": {}}])
        tab.goto("https://example.test", settle=(0, 0))
        self.assertIsNone(tab.last_error)

    def test_20260922_http_rejection_does_not_wait_for_load_or_settle(self):
        for status in (403, 429):
            with self.subTest(status=status):
                tab = tab_with(
                    [
                        {"id": 1, "result": {"loaderId": "main"}},
                        {
                            "method": "Network.responseReceived",
                            "params": {
                                "type": "Document",
                                "loaderId": "main",
                                "requestId": "main-request",
                                "response": {"status": status},
                            },
                        },
                    ]
                )
                tab.goto("https://example.test", settle=(3, 3))
                self.assertEqual(tab.last_status, status)
                self.assertIsNone(tab.last_error, "HTTP denial must not become a transport timeout")
                self.assertFalse(tab.last_ready)

    def test_20260922_failed_iframe_does_not_mark_main_navigation_failed(self):
        tab = tab_with(
            [
                {"id": 1, "result": {"loaderId": "main"}},
                {
                    "method": "Network.responseReceived",
                    "params": {
                        "type": "Document",
                        "loaderId": "main",
                        "requestId": "main-request",
                        "response": {"status": 200},
                    },
                },
                {
                    "method": "Network.loadingFailed",
                    "params": {
                        "type": "Document",
                        "requestId": "iframe",
                        "errorText": "net::ERR_ABORTED",
                    },
                },
                {"method": "Page.loadEventFired"},
            ]
        )
        tab.goto("https://example.test", settle=(0, 0))
        self.assertEqual(tab.last_status, 200)
        self.assertIsNone(tab.last_error)

    def test_20260922_home_dom_ready_does_not_wait_for_unrelated_assets(self):
        tab = tab_with(
            [
                {"id": 1, "result": {}},
                {"method": "Page.domContentEventFired"},
                {"method": "Fetch.requestPaused", "params": {"requestId": "sensor", "resourceType": "XHR"}},
            ]
        )
        tab.goto("https://example.test", settle=(0, 0), dom_only=True)
        self.assertIsNone(tab.last_error)
        tab.pump(0.005)
        self.assertTrue(any(message.get("method") == "Fetch.continueRequest" for message in tab.ws.sent))

    def test_submit_services_late_fetch(self):
        tab = tab_with(
            [
                {"method": "Page.loadEventFired"},
                {"id": 1, "result": {"result": {"value": "form-submit"}}},
                {"method": "Fetch.requestPaused", "params": {"requestId": "late", "resourceType": "XHR"}},
            ]
        )
        tab.submit_search("q", settle=(0.005, 0.005))
        self.assertTrue(any(message.get("method") == "Fetch.continueRequest" for message in tab.ws.sent))

    def test_command_deadline_is_not_extended_by_events(self):
        ticks = iter([0, 0.1, 0.2, 0.4, 0.6, 1.1, 1.2, 1.3, 1.4])
        tab = tab_with([{"method": "Network.dataReceived"}] * 10)
        tab.deadline = 1
        with patch.object(cb.time, "monotonic", side_effect=lambda: next(ticks)):
            with self.assertRaises(TimeoutError):
                tab.call("Runtime.evaluate", expression="1")
        self.assertTrue(all(0 < timeout <= 1 for timeout in tab.ws.timeouts))

    def test_failed_context_setup_disposes_context(self):
        from unittest.mock import MagicMock

        wire = MagicMock()
        wire.recv.side_effect = [
            json.dumps({"id": 1, "result": {"browserContextId": "ctx"}}),
            json.dumps({"id": 2, "error": {"message": "setup failed"}}),
            json.dumps({"id": 3, "result": {}}),
        ]
        with (
            patch.object(cb, "_http", return_value={"webSocketDebuggerUrl": "ws://localhost"}),
            patch("websocket.create_connection", return_value=wire),
        ):
            with self.assertRaises(RuntimeError):
                cb.open_incognito_tab()
        commands = [json.loads(call.args[0]) for call in wire.send.call_args_list]
        self.assertTrue(commands[0]["params"]["disposeOnDetach"])
        self.assertEqual(commands[-1]["method"], "Target.disposeBrowserContext")
        wire.close.assert_called_once()


class Metrics(unittest.TestCase):
    def test_deadline_boundaries_and_safe_errors(self):
        now = [0.0]
        trace = Trace(timeout=1, clock=lambda: now[0])
        for point, valid in ((0.999, True), (1.0, False), (1.001, False)):
            with self.subTest(point=point):
                now[0] = point
                if valid:
                    self.assertGreater(trace.remaining(), 0)
                else:
                    with self.assertRaises(DeadlineExceeded):
                        trace.remaining()
        with self.assertRaises(ValueError), trace.stage("test"):
            raise ValueError("secret-password-cookie")
        encoded = json.dumps(trace.snapshot())
        self.assertNotIn("secret-password-cookie", encoded)
        self.assertIn("ValueError", encoded)


class ParseContract(unittest.TestCase):
    def test_autocomplete_error_json_is_not_empty_success(self):
        from unittest.mock import Mock

        session = Mock()
        session.get.return_value.status_code = 200
        for payload, expected in (({"error": "denied"}, False), ([], True), ([None], False)):
            with self.subTest(payload=payload):
                session.get.return_value.json.return_value = payload
                self.assertEqual(cs.autocomplete(session, "q", persist_cookies=False)["ok"], expected)

    def test_real_fixture_preserves_sixty_products(self):
        result = engine.parse_search("q", HTML, 200, final_url="https://www.coupang.com/np/search?q=q")
        self.assertEqual(result["count"], 60)
        self.assertTrue(result["related_ok"])

    def test_empty_200_is_not_success(self):
        self.assertEqual(engine.parse_search("q", "<html>loading</html>", 200)["outcome"], "load_error")

    def test_explicit_no_results_is_success(self):
        self.assertEqual(engine.parse_search("q", "<html>검색 결과가 없습니다</html>", 200)["outcome"], "no_results")

    def test_other_query_is_not_accepted(self):
        self.assertEqual(engine.parse_search("q", HTML, 200, final_url="https://x/?q=other")["outcome"], "load_error")


class FakeTab:
    last_status = 200
    last_ready = True

    def goto(self, url, **kwargs):
        self.url = url

    def eval(self, expression):
        if "JSON.stringify" in expression:
            return json.dumps({"html": HTML, "url": self.url})
        return True

    def pump(self, seconds):
        pass

    def call(self, *args, **kwargs):
        pass


class SessionLifecycle(unittest.TestCase):
    def test_20260922_home_block_uses_bounded_retry_and_preserves_http_status(self):
        cases = (
            (True, (403, 200), "ok", 2, False),
            (True, (403, 403), "http_error", 2, False),
            (True, (429,), "http_error", 1, True),
            (False, (403,), "http_error", 1, True),
        )
        for proxy, statuses, outcome, attempts, paused in cases:
            with self.subTest(proxy=proxy, statuses=statuses):
                opened, closed, warmed, navigations = [], [], [], []

                class HomeTab(FakeTab):
                    def goto(self, url, *, events=navigations, **kwargs):
                        events.append(url)
                        super().goto(url, **kwargs)

                def factory(*args, states=statuses, tabs=opened, disposed=closed, **kwargs):
                    tab = HomeTab()
                    tab.last_status = states[len(tabs)]
                    tabs.append(tab)
                    return tab, lambda: disposed.append(tab)

                browser = engine.BrowserSearch()
                with (
                    patch.object(cb, "PROXY", {"server": "test"} if proxy else None),
                    patch.object(cb, "chrome_alive", return_value=True),
                    patch.object(cb, "open_incognito_tab", factory),
                    patch.object(
                        cb, "warm_context", side_effect=lambda tab, seen=warmed, **kw: seen.append(tab) or True
                    ),
                    patch.object(cs, "admit_request", return_value=1),
                    patch.object(cs, "paused_remaining", return_value=0),
                    patch.object(cs, "today_count", return_value=0),
                    patch.object(cs, "search_gate", side_effect=lambda **kw: contextlib.nullcontext()),
                    patch.object(cs, "maybe_pause_on_block") as pause,
                ):
                    result = browser.search("q", Trace(), tries=2)
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(result["attempts"], attempts)
                self.assertEqual(len(opened), attempts)
                self.assertEqual(len(warmed), int(outcome == "ok"), "blocked homes must skip warmup")
                self.assertEqual(len(navigations), attempts + int(outcome == "ok"))
                self.assertEqual(pause.called, paused)
                if outcome != "ok":
                    self.assertEqual(result["http_status"], statuses[-1])
                    self.assertEqual(result["failed_stage"], "home")
                    self.assertEqual(len(closed), attempts)
                browser.close()

    def test_reuses_successful_context_and_expires(self):
        opened = []
        closed = []

        def factory(*args, **kwargs):
            tab = FakeTab()
            opened.append(tab)
            return tab, lambda: closed.append(tab)

        browser = engine.BrowserSearch(ttl=60)
        with (
            patch.object(cb, "chrome_alive", return_value=True),
            patch.object(cb, "open_incognito_tab", factory),
            patch.object(cb, "warm_context", return_value=True),
            patch.object(cs, "admit_request", return_value=1),
            patch.object(cs, "paused_remaining", return_value=0),
            patch.object(cs, "today_count", return_value=0),
            patch.object(cs, "search_gate", side_effect=lambda **kw: contextlib.nullcontext()),
        ):
            first = browser.search("q", Trace())
            second = browser.search("next", Trace())
            self.assertFalse(first["session_reused"])
            self.assertTrue(second["session_reused"])
            self.assertEqual(len(opened), 1)
            browser.created -= 61
            third = browser.search("last", Trace())
            self.assertFalse(third["session_reused"])
            self.assertEqual(len(opened), 2)
            browser.close()
        self.assertEqual(len(closed), 2)

    def test_pause_prevents_new_context(self):
        browser = engine.BrowserSearch()
        with patch.object(cs, "paused_remaining", return_value=5), patch.object(cb, "open_incognito_tab") as opened:
            result = browser.search("q", Trace())
        self.assertEqual(result["outcome"], "paused")
        opened.assert_not_called()

    def test_gap_wait_cannot_exceed_deadline(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cs, "HOME", Path(temp)):
            (Path(temp) / "last_search").write_text(str(time.time()))
            with self.assertRaises(TimeoutError), cs.search_gate(min_gap=5, timeout=0.01):
                self.fail("navigation must not start beyond its deadline")

    def test_direct_block_pauses_without_retry(self):
        browser = engine.BrowserSearch()
        browser.tab = FakeTab()
        browser.tab.last_status = 403
        browser.created = time.monotonic()
        with (
            patch.object(cb, "PROXY", None),
            patch.object(cs, "admit_request", return_value=1),
            patch.object(cs, "search_gate", side_effect=lambda **kw: contextlib.nullcontext()),
            patch.object(cs, "maybe_pause_on_block") as pause,
        ):
            result = browser.search("q", Trace(), tries=2)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["outcome"], "http_error")
        pause.assert_called_once()
        self.assertIsNone(browser.tab)


class Jobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_duplicate_ids_keywords_and_queue_limit(self):
        manager = JobManager(self.home, max_pending=1, start=False)
        request_id = str(uuid.uuid4())
        first = manager.start("q", request_id)
        self.assertEqual(manager.start("q", request_id), first)
        self.assertEqual(manager.start("q", str(uuid.uuid4())), first)
        self.assertEqual(manager.start("other", request_id)["error"], "request_id_conflict")
        self.assertEqual(manager.start("other", str(uuid.uuid4()))["error"], "busy")
        self.assertEqual(manager.start(" ", str(uuid.uuid4()))["error"], "badinput")

    def test_restart_marks_unfinished_job_failed(self):
        first = JobManager(self.home, start=False)
        request_id = str(uuid.uuid4())
        first.start("q", request_id)
        second = JobManager(self.home, start=False)
        result = second.get(request_id)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "worker_restarted")
        self.assertEqual(second.start("q", request_id), result)
        self.assertTrue(second.pending.empty())

    def test_finished_auxiliary_results_survive_browser_deadline(self):
        now = [0.0]
        trace = Trace(timeout=1, clock=lambda: now[0])
        ready = threading.Event()
        seen = []

        class Browser:
            def search(self, query, trace):
                self_test.assertTrue(ready.wait(1))
                now[0] = 2
                return {"outcome": "timeout", "items": []}

        self_test = self

        def publish(section, status, data):
            seen.append((section, status))
            if ("autocomplete", "ok") in seen and ("sourcing", "ok") in seen:
                ready.set()

        ac = {"ok": True, "items": ["already available"], "used": "q", "outcome": "ok"}
        with (
            patch("cpk_engine.autocomplete", return_value=ac),
            patch("cpk_domeggook.check_existence", return_value={"exists": False}),
        ):
            response = run_job("q", trace, Browser(), publish, db_path=self.home / "market.db")
        self.assertTrue(response["result"]["autocomplete_ok"])
        self.assertEqual(response["result"]["autocomplete"], ["already available"])
        self.assertNotIn(("autocomplete", "timeout"), seen)
        self.assertNotIn(("sourcing", "timeout"), seen)

    def test_progress_is_visible_before_completion_and_survives_restart(self):
        release = threading.Event()
        ready = threading.Event()

        class IdleEngine:
            def idle(self):
                pass

            def close(self):
                pass

        def runner(query, trace, browser, publish, **kwargs):
            publish("autocomplete", "ok", {"ok": True, "items": ["first"]})
            ready.set()
            release.wait(2)
            publish("search", "ok", {"outcome": "ok", "items": []})
            publish("sourcing", "load_error", {})
            return {}

        manager = JobManager(self.home, engine_factory=IdleEngine, runner=runner, timeout=3)
        self.addCleanup(manager.close)
        self.addCleanup(release.set)
        request_id = str(uuid.uuid4())
        manager.start("q", request_id)
        self.assertTrue(ready.wait(1))
        self.assertEqual(manager.get(request_id)["autocomplete"]["items"], ["first"])
        self.assertEqual(manager.get(request_id)["state"], "running")
        release.set()
        manager.pending.join()
        self.assertEqual(manager.get(request_id)["state"], "partial")
        self.assertIn("essential_ready_ms", manager.get(request_id)["timing"]["milestones"])

    def test_browser_runs_while_sourcing_and_autocomplete_are_pending(self):
        browser_started = threading.Event()
        seen = []

        class Browser:
            def search(self, query, trace):
                browser_started.set()
                return engine.parse_search(query, HTML, 200)

        def ac(query, trace):
            self.assertTrue(browser_started.wait(1))
            return {"ok": False, "items": [], "outcome": "load_error", "used": query}

        def source(query, **kwargs):
            self.assertTrue(browser_started.wait(1))
            return {"exists": False, "count": 0, "market": "dome"}

        with patch("cpk_engine.autocomplete", ac), patch("cpk_domeggook.check_existence", source):
            response = run_job(
                "q", Trace(), Browser(), lambda *event: seen.append(event), db_path=self.home / "market.db"
            )
        self.assertFalse(response["result"]["autocomplete_ok"])
        self.assertEqual(response["result"]["count"], 60)
        self.assertTrue(any(section == "search" and status == "ok" for section, status, _ in seen))

    def test_browser_events_are_pumped_while_auxiliary_results_are_pending(self):
        pumped = threading.Event()

        class Browser:
            def search(self, query, trace):
                return {"outcome": "no_results", "items": []}

            def idle(self):
                pumped.set()

        def ac(query, trace):
            return {"ok": pumped.wait(1), "items": [], "outcome": "ok", "used": query}

        with (
            patch("cpk_engine.autocomplete", ac),
            patch("cpk_domeggook.check_existence", return_value={"exists": False}),
        ):
            response = run_job("q", Trace(), Browser(), lambda *event: None, db_path=self.home / "market.db")
        self.assertTrue(pumped.is_set())
        self.assertTrue(response["result"]["autocomplete_ok"])


if __name__ == "__main__":
    unittest.main()
