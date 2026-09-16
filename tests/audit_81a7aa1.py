"""Manual offline audit: python tests/audit_81a7aa1.py (macOS/Linux).

Uses temporary state, fake HTTP/CDP, real file locks, threads and processes.
Never connects to Chrome/Coupang or sends notifications. Prints expected vs
observed values; exits 1 when the reviewed guarantees are not satisfied.
Kept outside unittest's test_* discovery as a review reproduction artifact.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
import urllib.request


def main():
    if sys.platform == "win32":
        raise SystemExit("Run on macOS/Linux: CPK file locking is disabled on Windows")
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    observations = []

    def check(name, actual, expected):
        passed = all(actual.get(k) == v for k, v in expected.items())
        record = dict(case=name, passed=passed, actual=actual, expected=expected)
        observations.append(record)
        print(json.dumps(record, sort_keys=True), flush=True)

    with tempfile.TemporaryDirectory(prefix="cpk_audit_81a7aa1_") as scratch:
        with mock.patch.dict(os.environ, CPK_HOME=scratch, CPK_NTFY_TOPIC="",
                             CPK_SYNC_TARGET="", CPK_SEARCH_MIN_GAP="0"):
            import cpk_session as cs
            import cpk_browser as cb
            import cpk_collect as cc
            import cpk_keepalive as ka
            import cpk_import as ci
            import cpk_search as sr
            from curl_cffi import requests

            gotos, http_calls = [], []
            fixture = (repo / "tests/fixtures/search_sample.html").read_text(encoding="utf-8")
            scenario = dict(status=403, units=0)

            class Response:
                def __init__(self):
                    self.status_code = scenario["status"]
                    self.text = fixture if scenario["units"] else "<html>Access denied</html>"
                    self.content = self.text.encode()

                def json(self):
                    return []

            class Session:
                cookies = type("Cookies", (), {"jar": []})()

                def get(self, url, **kwargs):
                    http_calls.append(url)
                    return Response()

            class FakeTab:
                last_status = None
                last_error = None

                def goto(self, url, **kwargs):
                    gotos.append(url)
                    self.last_status = scenario["status"]

                def eval(self, expr):
                    if "ProductUnit" in expr:
                        return scenario["units"]
                    if "outerHTML" in expr:
                        return Response().text
                    return "fake"

                def cookies(self):
                    return [{"name": "_abck", "value": "fake", "domain": ".coupang.com"}]

                def close(self):
                    pass

            def fresh():
                home = Path(tempfile.mkdtemp(dir=scratch, prefix="case_"))
                os.environ["CPK_HOME"] = str(home)
                cs.HOME, cs.JAR = home, home / "jar.json"
                cs.HEALTH, cs.LOG = home / "health.json", home / "keepalive.log"
                cs.SEARCH_MIN_GAP, cs.DAILY_BUDGET = 0, 100
                cs.reset_ua_override()
                cb.UA_FILE, cb.PROFILE = home / "ua.json", home / "chrome-profile"
                cc.STATE, cc.DATA, cc.KEYWORDS = home / "collect.json", home / "data", home / "keywords.txt"
                cc.MODE, cc.QUIET, cc.SYNC_TARGET = "browser", "", ""
                cc.GAP_MIN = cc.GAP_MAX = 0
                cc.KEYWORDS.write_text("q\n")
                scenario.update(status=403, units=0)
                gotos.clear()
                http_calls.clear()
                return home

            def bump(h):
                time.sleep(0.005)  # Widen an actual read/modify/write race.
                h["runs"] = h.get("runs", 0) + 1
                h["fails"] = h.get("fails", 0) + 1
                return h

            with contextlib.ExitStack() as stack:
                for target, attr, kwargs in [
                    (requests.Session, "request", dict(side_effect=AssertionError("HTTP forbidden"))),
                    (urllib.request, "urlopen", dict(side_effect=AssertionError("URL forbidden"))),
                    (socket.socket, "connect", dict(side_effect=AssertionError("socket forbidden"))),
                    (cb, "Tab", dict(new=FakeTab)), (cb, "chrome_alive", dict(return_value=True)),
                    (cb, "available", dict(return_value=True)),
                    (cb, "launch_chrome", dict(side_effect=AssertionError("Chrome forbidden"))),
                    (cb, "quit_chrome", dict(side_effect=AssertionError("Chrome forbidden"))),
                    (cs, "make_session", dict(side_effect=Session)),
                    (cs, "_throttle", dict(return_value=None)),
                    (cs, "log", dict(return_value=None)), (ka, "notify", dict(return_value=None)),
                ]:
                    stack.enter_context(mock.patch.object(target, attr, **kwargs))

                for state in ("paused", "budget"):
                    fresh()
                    if state == "paused":
                        cs.pause(3600, "audit")
                    else:
                        cs.DAILY_BUDGET = 0
                    r = cb.search("q")
                    ka.main()
                    cb.harvest("q")
                    cs.autocomplete_keywords("q")
                    check(state + "_browser_health_harvest_autocomplete",
                          dict(outcome=r["outcome"], gotos=len(gotos), http=len(http_calls), counted=cs.today_count()),
                          dict(outcome=state, gotos=0, http=0, counted=0))

                fresh()
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = sr.main()
                check("legacy_cli_closed", dict(rc=rc, http=len(http_calls)), dict(rc=2, http=0))

                fresh()
                entered = threading.Event()
                original_admit = cs.admit_request

                def signal_admit(*args, **kwargs):
                    entered.set()
                    return original_admit(*args, **kwargs)

                def waiting_request():
                    try:
                        with cs.request_gate("audit"):
                            return "admitted"
                    except cs.RequestPaused:
                        return "paused"

                with mock.patch.object(cs, "admit_request", signal_admit):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        with cs._flock("control.lock"):
                            future = pool.submit(waiting_request)
                            if not entered.wait(5):
                                raise RuntimeError("control race setup timed out")
                            cs._write_control(dict(paused_until=time.time() + 3600, reason="committed under lock"))
                        result = future.result(timeout=10)
                check("pause_during_control_lock_wait", dict(result=result, count=cs.today_count()),
                      dict(result="paused", count=0))

                fresh()
                barrier = threading.Barrier(4)

                def budget_race(_):
                    barrier.wait(timeout=5)
                    try:
                        cs.admit_request("audit", limit=1)
                        return 1
                    except cs.BudgetExceeded:
                        return 0

                with ThreadPoolExecutor(max_workers=4) as pool:
                    accepted = sum(pool.map(budget_race, range(4)))
                check("four_threads_budget_one", dict(accepted=accepted, count=cs.today_count()), dict(accepted=1, count=1))

                fresh()
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(lambda _: cs.update_health(bump), range(16)))
                check("health_thread_updates", cs.read_health(), dict(runs=16, fails=16))

                fresh()
                child = """
import socket, urllib.request, time
from unittest.mock import patch
from curl_cffi import requests
import cpk_session as cs
def bump(h):
    time.sleep(0.005)
    h['runs'] = h.get('runs', 0) + 1
    h['fails'] = h.get('fails', 0) + 1
    return h
with patch.object(requests.Session, 'request', side_effect=AssertionError('HTTP forbidden')), patch.object(urllib.request, 'urlopen', side_effect=AssertionError('URL forbidden')), patch.object(socket.socket, 'connect', side_effect=AssertionError('socket forbidden')):
    for _ in range(4): cs.update_health(bump)
"""
                def run_child(_):
                    return subprocess.run([sys.executable, "-c", child], cwd=repo, capture_output=True, text=True, timeout=20)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    children = list(pool.map(run_child, range(3)))
                for child_result in children:
                    if child_result.returncode:
                        raise RuntimeError(child_result.stderr)
                check("health_three_process_updates", cs.read_health(), dict(runs=12, fails=12))

                fresh()
                r = cb.search("q")
                check("ondemand_403_propagates", dict(outcome=r["outcome"], paused=cs.paused_remaining() > 0, http=len(http_calls)),
                      dict(outcome="http_error", paused=True, http=0))

                fresh()
                r = cs.autocomplete_keywords("one two three")
                check("initial_autocomplete_403_is_failure", dict(ok=r["ok"], calls=len(http_calls)), dict(ok=False, calls=1))

                fresh()
                cs._write_control(dict(day="2000-01-01", counts=dict(collect=100)))
                old_count = cs.today_count()
                with cs.request_gate("audit"):
                    pass
                check("budget_date_rollover", dict(old_count=old_count, new_count=cs.today_count()), dict(old_count=0, new_count=1))

                fresh()
                cs.pause(3600, "audit")
                with mock.patch.object(sys, "argv", ["cpk_import.py", "-"]), mock.patch.object(sys, "stdin", io.StringIO("PCID=fake")):
                    ci.main()
                check("paused_cookie_import_verification", dict(http=len(http_calls), counted=cs.today_count()), dict(http=0, counted=0))

                fresh()
                scenario.update(status=200, units=60)
                original_harvest = cb.harvest

                def pause_after_harvest(*args, **kwargs):
                    res = original_harvest(*args, **kwargs)
                    cs.pause(3600, "pause before verification")
                    return res

                with mock.patch.object(cb, "harvest", pause_after_harvest):
                    result = cb.reissue("q", verify=True)
                check("paused_reissue_verification", dict(http=len(http_calls), result=result, counted=cs.today_count()), dict(http=0, result=False))

                fresh()
                cb.harvest("q")
                check("harvest_403_propagates", dict(gotos=len(gotos), paused=cs.paused_remaining() > 0), dict(paused=True))

                fresh()
                cs.autocomplete_keywords("q")
                check("autocomplete_403_propagates", dict(http=len(http_calls), paused=cs.paused_remaining() > 0), dict(paused=True))

                home = fresh()
                cs.DAILY_BUDGET = 2  # One warmup and one failed search; no retry budget.
                r = cb.search("q")
                check("search_403_before_budget_exhaustion", dict(outcome=r["outcome"], status=r["http_status"], paused=cs.paused_remaining() > 0,
                      evidence=len(list((home / "evidence").glob("*.json")))), dict(status=403, paused=True, evidence=1))

                fresh()
                main_thread = threading.get_ident()
                read_done, continue_reader = threading.Event(), threading.Event()
                original_read = cc.read_state

                def delayed_read():
                    st = original_read()
                    if threading.get_ident() != main_thread:
                        read_done.set()
                        if not continue_reader.wait(5):
                            raise RuntimeError("collector race setup timed out")
                    return st

                @contextlib.contextmanager
                def fake_session(*args, **kwargs):
                    yield lambda *a, **k: None

                with mock.patch.object(cc, "read_state", delayed_read), mock.patch.object(cb, "search_session", fake_session), mock.patch.object(cc, "_run_loop", return_value=(0, 0, 0, None)):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        later = pool.submit(cc.run)
                        try:
                            if not read_done.wait(5):
                                raise RuntimeError("collector reader did not start")
                            first_rc = cc.run()
                        finally:
                            continue_reader.set()
                        second_rc = later.result(timeout=10)
                check("collector_stale_read_before_run_lock", dict(first_rc=first_rc, second_rc=second_rc, runs=cc.read_state()["runs_today"]),
                      dict(first_rc=0, second_rc=0, runs=2))

    failures = sum(not record["passed"] for record in observations)
    print(json.dumps(dict(cases=len(observations), failed=failures, passed=len(observations) - failures)), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
