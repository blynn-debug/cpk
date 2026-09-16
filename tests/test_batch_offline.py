"""100-keyword batch simulation, with no live HTTP or Chrome access.

Run: python -m unittest tests.test_batch_offline -v
Real parsing, request accounting and result storage; fake CDP/HTTP responses.
Waits are zeroed only in this test. Runtime is not a live throughput estimate.
"""
import contextlib
import io
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.parse
import urllib.request

import cpk_browser as cb
import cpk_collect as cc
import cpk_session as cs


class HundredKeywordBatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cpk_batch_simulation_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.data = self.root / "data"
        self.queries = [f"mock-keyword-{i:03d}" for i in range(1, 101)]
        keyword_file = self.root / "keywords.txt"
        keyword_file.write_text("\n".join(self.queries), encoding="utf-8")
        fixtures = Path(__file__).resolve().parent / "fixtures"
        self.html = (fixtures / "search_sample.html").read_text(encoding="utf-8")
        self.expected = json.loads((fixtures / "search_sample.expected.json").read_text(encoding="utf-8"))
        self.searches = []
        self.autocompletes = []
        self.home_visits = []
        self.block_query = None
        owner = self

        class FakeTab:
            last_status = None
            last_error = None

            def goto(self, url, **kwargs):
                self.url = url
                if url == cs.HOME_URL:
                    owner.home_visits.append(url)
                    self.last_status, self.units, self.html = 200, 0, "<html>home</html>"
                    return
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0]
                owner.searches.append(query)
                if query == owner.block_query:
                    self.last_status, self.units, self.html = 403, 0, "<html>Access denied</html>"
                else:
                    self.last_status, self.units, self.html = 200, owner.expected["count"], owner.html

            def eval(self, expr):
                if "ProductUnit" in expr:
                    return self.units
                if "outerHTML" in expr:
                    return self.html
                if "location" in expr:
                    return self.url
                return "Simulated search"

            def close(self):
                pass

        class FakeSession:
            def __init__(self):
                self.cookies = SimpleNamespace(jar=[])

            def get(self, url, **kwargs):
                parsed = urllib.parse.urlsplit(url)
                if parsed.path != "/n-api/web-adapter/search":
                    raise AssertionError("Unexpected fake HTTP endpoint")
                query = urllib.parse.parse_qs(parsed.query)["keyword"][0]
                owner.autocompletes.append(query)
                items = [{"keyword": f"{query}-suggestion-{i}", "travelKeyword": False} for i in range(10)]
                return SimpleNamespace(status_code=200, json=lambda: items)

        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for module, attr, value in [
            (cs, "HOME", self.state), (cs, "JAR", self.state / "jar.json"),
            (cs, "HEALTH", self.state / "health.json"), (cs, "LOG", self.state / "keepalive.log"),
            (cs, "SEARCH_MIN_GAP", 0), (cs, "DAILY_BUDGET", 300),
            (cs, "_UA_OVERRIDE", {}),
            (cc, "STATE", self.state / "collect.json"), (cc, "DATA", self.data),
            (cc, "KEYWORDS", keyword_file), (cc, "MODE", "browser"),
            (cc, "QUIET", ""), (cc, "SYNC_TARGET", ""),
            (cc, "GAP_MIN", 0), (cc, "GAP_MAX", 0),
            (cb, "PROFILE", self.state / "chrome-profile"), (cb, "UA_FILE", self.state / "ua.json"),
            (cb, "Tab", FakeTab), (cs, "make_session", FakeSession),
        ]:
            self.patches.enter_context(mock.patch.object(module, attr, value))
        self.patches.enter_context(mock.patch.object(cb, "available", return_value=True))
        self.patches.enter_context(mock.patch.object(cb, "chrome_alive", return_value=True))
        self.forbidden = []
        for obj, attr in [(cs.requests.Session, "request"), (urllib.request, "urlopen"),
                          (socket.socket, "connect"), (cb, "launch_chrome"), (cb, "quit_chrome")]:
            guard = self.patches.enter_context(mock.patch.object(obj, attr, side_effect=AssertionError("Live access forbidden")))
            self.forbidden.append(guard)

    def tearDown(self):
        for guard in self.forbidden:
            guard.assert_not_called()

    def run_batch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return cc.run()

    def rows(self):
        return [json.loads(line) for line in (self.data / "runs.jsonl").read_text(encoding="utf-8").splitlines()]

    def assert_saved_results(self, queries):
        latest = list((self.data / "latest").glob("*.json"))
        self.assertEqual(len(latest), len(queries))
        self.assertEqual(len(list((self.data / cc.today()).glob("*.json"))), len(queries))
        run_ids = set()
        for query in queries:
            name = cc.safe_name(query) + ".json"
            contents = (self.data / "latest" / name).read_text(encoding="utf-8")
            self.assertEqual(contents, (self.data / cc.today() / name).read_text(encoding="utf-8"))
            result = json.loads(contents)
            self.assertEqual(result["query"], query)
            self.assertEqual(result["related_keywords"], self.expected["related"])
            self.assertEqual(result["autocomplete"], [f"{query}-suggestion-{i}" for i in range(10)])
            self.assertEqual(result["count"], 60)
            self.assertEqual(len(result["items"]), 60)
            self.assertTrue(all(item["product_id"] and item["name"] and item["price"] for item in result["items"]))
            run_ids.add(result["run_id"])
        self.assertEqual(len(run_ids), 1)

    def test_100_keywords_save_all_three_outputs(self):
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.searches, self.queries)
        self.assertEqual(self.autocompletes, self.queries)
        self.assert_saved_results(self.queries)
        rows = self.rows()
        self.assertEqual([r["query"] for r in rows], self.queries)
        self.assertTrue(all(r["outcome"] == "ok" for r in rows))
        self.assertEqual(cs.read_control()["counts"], {"warmup": 1, "collect": 100, "autocomplete": 100})
        self.assertEqual(cc.read_state()["last_result"]["ok"], 100)
        print("SIMULATION: 100/100 saved; related + autocomplete + 60 products each; 201 gate requests; live requests 0")

    def test_block_at_keyword_50_preserves_first_49_and_stops(self):
        self.block_query = self.queries[49]
        self.assertEqual(self.run_batch(), 1)
        self.assertEqual(self.searches, self.queries[:49] + [self.block_query] * 3)
        self.assertEqual(self.autocompletes, self.queries[:49])
        self.assert_saved_results(self.queries[:49])
        rows = self.rows()
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[-1]["http_status"], 403)
        self.assertEqual(rows[-1]["query"], self.block_query)
        self.assertGreater(cs.paused_remaining(), 0)
        before = (len(self.searches), len(self.autocompletes), cs.today_count())
        self.assertEqual(self.run_batch(), 4)
        self.assertEqual((len(self.searches), len(self.autocompletes), cs.today_count()), before)
        print("SIMULATION: block at #50 -> first 49 saved; #51-100 untouched; rerun during pause sends 0")

    def test_budget_stops_after_50_complete_keywords(self):
        cs.DAILY_BUDGET = 101
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.searches, self.queries[:50])
        self.assertEqual(self.autocompletes, self.queries[:50])
        self.assert_saved_results(self.queries[:50])
        self.assertEqual(cs.today_count(), 101)
        self.assertEqual(cc.read_state()["last_result"]["stopped"], "budget")
        self.assertEqual(len(self.rows()), 50)
        print("SIMULATION: budget 101 -> first 50 fully saved; no over-budget request")


if __name__ == "__main__":
    unittest.main(verbosity=2)
