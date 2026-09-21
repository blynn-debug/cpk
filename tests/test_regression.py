"""리그레션 테스트 — 이번 세션에서 실제로 잡은 버그가 재발하지 않도록 고정.
네트워크 없음(모두 목/정적 검사). 실행: python -m unittest tests.test_regression
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

import sys

import cpk_session as cs
import cpk_browser as cb
import cpk_keepalive as ka
import cpk_collect as cc


def _tmp_home() -> Path:
    d = Path(tempfile.mkdtemp(prefix="cpk_reg_"))
    cs.HOME = d
    cs.JAR = d / "jar.json"
    cs.HEALTH = d / "health.json"
    cs.LOG = d / "keepalive.log"
    return d


class PkillPattern(unittest.TestCase):
    """macOS pkill/pgrep -f 는 '--'로 시작하는 패턴을 옵션으로 오해한다.
    quit_chrome 의 패턴이 '--' 로 시작하면 안 된다(2026-09-16 버그)."""

    def test_pattern_no_leading_dashdash(self):
        src = Path(cb.__file__).read_text(encoding="utf-8")
        self.assertIn('pat = f"user-data-dir=', src)
        self.assertNotIn('pat = f"--user-data-dir=', src)


class ControlGuarantees(unittest.TestCase):
    """공통 제어의 원자성·날짜·상태 보존(REVIEW 87204b9 완료 기준)."""

    def setUp(self):
        _tmp_home()
        cs.SEARCH_MIN_GAP = 0
        cs.clear_pause()
        cs.DAILY_BUDGET = 1000

    def test_today_count_ignores_yesterday(self):
        # 어제 예산을 다 썼어도 오늘 카운트는 0 (전날 예산이 오늘을 막지 않음)
        cs._write_control({"day": "2000-01-01", "counts": {"collect": 999}})
        self.assertEqual(cs.today_count(), 0)

    def test_admit_paused_does_not_count(self):
        cs.pause(3600, "test")
        with self.assertRaises(cs.RequestPaused):
            cs.admit_request("collect", limit=cs.DAILY_BUDGET)
        self.assertEqual(cs.today_count(), 0)

    def test_admit_budget_not_counted_over_limit(self):
        cs.DAILY_BUDGET = 1
        cs.admit_request("collect", limit=1)
        with self.assertRaises(cs.BudgetExceeded):
            cs.admit_request("collect", limit=1)
        self.assertEqual(cs.today_count(), 1)  # 초과분 미집계

    def test_warmup_no_goto_when_budget_zero(self):
        # 예산 0이면 워밍업(홈 방문)도 검색도 페이지 이동 0 (관문 밖 홈 방문 문제)
        cs.DAILY_BUDGET = 0
        gotos = []

        class FakeTab:
            last_status = None
            last_error = None

            def goto(self, url, settle=(0, 0)):
                gotos.append(url)

            def eval(self, expr):
                return 0

            def close(self):
                pass

        orig_tab, orig_alive = cb.Tab, cb.chrome_alive
        cb.Tab = FakeTab
        cb.chrome_alive = lambda: True
        try:
            with cb.search_session("collect", warmup=True) as fetch:
                r = fetch("q", 1)
        finally:
            cb.Tab, cb.chrome_alive = orig_tab, orig_alive
        self.assertEqual(gotos, [], "예산 0인데 페이지 이동 발생")
        self.assertEqual(r["outcome"], "budget")

    def test_update_health_accumulates(self):
        def bump_fail(h):
            h["fails"] = int(h.get("fails", 0)) + 1
            h["runs"] = int(h.get("runs", 0)) + 1
            return h
        cs.update_health(bump_fail)
        cs.update_health(bump_fail)
        h = cs.read_health()
        self.assertEqual(h["fails"], 2)  # 두 번의 갱신이 누적(유실 없음)
        self.assertEqual(h["runs"], 2)

    def test_autocomplete_http_failure_flagged(self):
        # HTTP 403은 조회 실패(ok=False) — 정상 빈 결과가 아님, 축약도 하지 않음
        orig = cs.autocomplete
        cs.autocomplete = lambda s, q, referer=None: {"items": [], "status": 403, "ok": False}
        try:
            r = cs.autocomplete_keywords("틈새 곰팡이 방지 테이프")
        finally:
            cs.autocomplete = orig
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"], [])


class CollectRetryAfterBlock(unittest.TestCase):
    """수집 중 429가 나면 즉시 멈추지 말고, 브라우저 재발급 후 같은 검색어를 1회 재시도해야 한다."""

    def setUp(self):
        self.home = _tmp_home()
        cs.JAR.write_text('[{"name":"PCID","value":"x","domain":".coupang.com","path":"/","expires":null,"secure":true}]',
                          encoding="utf-8")
        self.data = Path(tempfile.mkdtemp(prefix="cpk_data_"))
        cc.DATA = self.data
        cc.STATE = self.home / "collect.json"
        kwf = self.home / "keywords.txt"
        kwf.write_text("방충망 잠금장치\n", encoding="utf-8")
        cc.KEYWORDS = kwf
        cc.QUIET = ""
        cc.SYNC_TARGET = ""
        cc.MODE = "python"
        cs.SEARCH_MIN_GAP = 0
        cc.GAP_MIN = cc.GAP_MAX = 0.0

    def test_block_then_reissue_then_success(self):
        fixture = (Path(__file__).resolve().parent / "fixtures" / "search_sample.html").read_text(encoding="utf-8")
        calls = {"n": 0, "reissued": 0}

        def fake_search_html(s, q, page=1):
            calls["n"] += 1
            return (429, "<div class='sec-if-cpt-container'>") if calls["n"] == 1 else (200, fixture)

        def fake_reissue(**kw):
            calls["reissued"] += 1
            return True

        import cpk_search as sr
        orig_sh, orig_ac = cs.search_html, cs.autocomplete
        orig_ri, orig_av = cb.reissue, cb.available
        cs.search_html = fake_search_html
        cs.autocomplete = lambda s, q, referer=None: []
        cb.reissue = fake_reissue
        cb.available = lambda: True
        try:
            rc = cc.run()
        finally:
            cs.search_html, cs.autocomplete = orig_sh, orig_ac
            cb.reissue, cb.available = orig_ri, orig_av

        self.assertEqual(calls["reissued"], 1, "재발급이 호출되지 않음")
        self.assertGreaterEqual(calls["n"], 2, "재시도가 없었음")
        self.assertEqual(rc, 0)
        files = list((self.data / "latest").glob("*.json"))
        self.assertTrue(files, "재시도 성공인데 결과 파일이 없음")


@unittest.skipUnless(sys.platform == "darwin", "fcntl 파일 잠금은 맥에서만 유효")
class BrowserLockConcurrent(unittest.TestCase):
    """Chrome 재발급은 한 번에 하나만. 이미 재발급 중이면 두 번째는 즉시 건너뛴다(포트·프로필 경쟁 방지)."""

    def test_second_reissue_skips_when_locked(self):
        _tmp_home()
        called = {"harvest": 0}
        orig_av, orig_h = cb.available, cb.harvest
        cb.available = lambda: True
        cb.harvest = lambda *a, **k: called.__setitem__("harvest", 1) or {}
        try:
            with cs.browser_lock() as got:
                self.assertTrue(got)
                # 락 보유 중 두 번째 재발급 → skip(False), Chrome 조작(harvest) 없음
                self.assertFalse(cb.reissue(verify=False))
            self.assertEqual(called["harvest"], 0, "락이 걸렸는데 두 번째가 Chrome을 건드림")
        finally:
            cb.available, cb.harvest = orig_av, orig_h


class PausedNoNewRequests(unittest.TestCase):
    """중단 상태에서는 모든 경로가 새 요청을 0건 보내야 한다(검색·자동완성·헬스)."""

    def setUp(self):
        _tmp_home()
        cs.SEARCH_MIN_GAP = 0
        cs.pause(3600, "test")

    def tearDown(self):
        cs.clear_pause()

    def test_request_gate_raises(self):
        with self.assertRaises(cs.RequestPaused):
            with cs.request_gate("collect"):
                pass

    def test_autocomplete_sends_nothing(self):
        calls = []
        orig = cs.autocomplete
        cs.autocomplete = lambda s, q, referer=None: calls.append(q) or []
        try:
            r = cs.autocomplete_keywords("계란보관함")
        finally:
            cs.autocomplete = orig
        self.assertEqual(calls, [], "중단 중인데 자동완성 API를 호출함")
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("blocked"))

    def test_browser_fetch_returns_paused(self):
        # 상시 크롬 재사용 경로에서 fetch가 중단이면 goto 없이 paused를 돌려준다
        gotos = []

        class FakeTab:
            last_status = None
            last_error = None

            def goto(self, url, settle=(0, 0)):
                gotos.append(url)

            def eval(self, expr):
                return 0

            def close(self):
                pass

        orig_tab, orig_alive = cb.Tab, cb.chrome_alive
        cb.Tab = FakeTab
        cb.chrome_alive = lambda: True
        try:
            with cb.search_session("collect", warmup=False) as fetch:
                r = fetch("q", 1)
        finally:
            cb.Tab, cb.chrome_alive = orig_tab, orig_alive
        self.assertEqual(r["outcome"], "paused")
        self.assertEqual(gotos, [], "중단 중인데 검색 페이지로 이동함")


class CdpEventRetention(unittest.TestCase):
    """CDP 이벤트를 수신 지점(call·goto·eval)에서 버리지 않고 메인 문서 상태·오류를 보존한다.
    2026-09-17 실패: 응답 상태가 유실되어 http_status=null 이던 문제(REVIEW audit A/B)."""

    def _wire(self, messages):
        class Wire:
            def __init__(self, msgs):
                self.messages = list(msgs)

            def send(self, body):
                pass

            def recv(self):
                if not self.messages:
                    raise TimeoutError("timeout")
                return json.dumps(self.messages.pop(0))

            def settimeout(self, s):
                pass
        t = cb.Tab.__new__(cb.Tab)
        t.seq, t.last_status, t.last_error, t._loader = 0, None, None, None
        t.ws = Wire(messages)
        return t

    @staticmethod
    def _resp(status=403):
        return {"method": "Network.responseReceived", "params": {
            "type": "Document", "frameId": "main", "loaderId": "current",
            "requestId": "d1", "response": {"status": status}}}

    @staticmethod
    def _ack(**extra):
        return {"id": 1, "result": {"frameId": "main", "loaderId": "current", **extra}}

    @staticmethod
    def _loaded():
        return {"method": "Page.loadEventFired", "params": {}}

    def test_response_before_ack_retained(self):
        t = self._wire([self._resp(), self._ack(), self._loaded()])
        t.goto("https://x/search", settle=(0, 0))
        self.assertEqual(t.last_status, 403)

    def test_late_response_during_eval_retained(self):
        t = self._wire([self._ack(), self._loaded(), self._resp(),
                        {"id": 2, "result": {"result": {"value": "<html>ok</html>"}}}])
        t.goto("https://x/search", settle=(0, 0))
        html = t.eval("document.documentElement.outerHTML")
        self.assertEqual(t.last_status, 403)  # eval 중 도착한 응답도 반영
        self.assertEqual(html, "<html>ok</html>")

    def test_navigate_error_retained(self):
        t = self._wire([self._ack(errorText="net::ERR_NAME_NOT_RESOLVED"), self._loaded()])
        t.goto("https://x/search", settle=(0, 0))
        self.assertEqual(t.last_error, "net::ERR_NAME_NOT_RESOLVED")

    def test_transport_timeout_explained(self):
        t = self._wire([self._ack()])  # loaded 없이 끊김
        t.goto("https://x/search", settle=(0, 0))
        self.assertTrue(t.last_error)


class ClassifyOutcome(unittest.TestCase):
    """상품 0개를 무조건 403으로 뭉개던 버그(P1-1)가 재발하지 않도록 결과 유형 판정을 고정."""

    def test_ok_when_products(self):
        self.assertEqual(cb.classify_outcome(200, "<html></html>", 60), "ok")

    def test_no_results_on_clean_200(self):
        self.assertEqual(cb.classify_outcome(200, "<html>결과 없음</html>", 0), "no_results")

    def test_challenge_before_status(self):
        # 봇 검증 화면은 200이든 429든 challenge로
        self.assertEqual(cb.classify_outcome(200, "<div class='sec-if-cpt-container'></div>", 0), "challenge")
        self.assertEqual(cb.classify_outcome(429, "<div class='sec-if-cpt-container'></div>", 0), "challenge")

    def test_access_denied_403_template_is_challenge(self):
        # 2026-09-17 실측: 검색이 상품 대신 error403 접근 제한 HTML을 받았는데 load_error 로 오판했다.
        html = '<html><head><title>Error 403</title></head><body><div id="error403">' \
               '사용권한이 없습니다</div></body></html>'
        self.assertEqual(cb.classify_outcome(None, html, 0), "challenge")  # 상태 null 이어도 차단으로 판정

    def test_http_error(self):
        self.assertEqual(cb.classify_outcome(503, "<html>err</html>", 0), "http_error")

    def test_load_error_when_no_status(self):
        self.assertEqual(cb.classify_outcome(None, "", 0), "load_error")


class BrowserFetchOffline(unittest.TestCase):
    """browser 경로 fetch의 재시도·결과 분류를 네트워크 없이(가짜 CDP 탭) 검증한다.
    운영 Chrome을 건드리지 않고 실패 분류·재시도 로직을 오프라인에서 커버(REVIEW P2-7 권고)."""

    def setUp(self):
        self.home = _tmp_home()
        cs.SEARCH_MIN_GAP = 0
        cs.clear_pause()
        cs.DAILY_BUDGET = 1000

    def _run(self, steps):
        """steps: 각 goto가 만들 (status, units, html). fetch 결과 dict 반환."""
        state = {"i": 0}

        class FakeTab:
            last_status = None
            last_error = None

            def goto(self, url, settle=(0, 0)):
                s = steps[min(state["i"], len(steps) - 1)]
                state["i"] += 1
                self.__class__.last_status = s[0]
                self._units, self._html = s[1], s[2]

            def eval(self, expr):
                if "ProductUnit" in expr:
                    return self._units
                if "outerHTML" in expr:
                    return self._html
                if "location" in expr:
                    return "u"
                return "t"

            def close(self):
                pass

        orig_tab, orig_alive = cb.Tab, cb.chrome_alive
        cb.Tab = FakeTab
        cb.chrome_alive = lambda: True  # 상시 크롬 재사용 경로(락 없이 탭만)
        try:
            with cb.search_session("test", warmup=False) as fetch:
                r = fetch("q", 1)
        finally:
            cb.Tab, cb.chrome_alive = orig_tab, orig_alive
        return r, state["i"]

    def test_ok_single_attempt(self):
        r, n = self._run([(200, 60, "<html>ok</html>")])
        self.assertEqual(r["outcome"], "ok")
        self.assertEqual(n, 1)

    def test_no_results_no_retry(self):
        r, n = self._run([(200, 0, "<html>결과 없음</html>")])
        self.assertEqual(r["outcome"], "no_results")
        self.assertEqual(n, 1)  # 무결과는 재시도 안 함

    def test_challenge_then_ok_retries(self):
        r, n = self._run([(200, 0, "<div class='sec-if-cpt-container'></div>"),
                          (200, 60, "<html>ok</html>")])
        self.assertEqual(r["outcome"], "ok")
        self.assertEqual(n, 2)  # 챌린지 후 재시도해 성공

    def test_http_error(self):
        r, n = self._run([(503, 0, "<html>err</html>")] * 3)
        self.assertEqual(r["outcome"], "http_error")

    def test_evidence_saved_on_failure(self):
        self._run([(503, 0, "<html>err</html>")] * 3)
        ev = list((cs.HOME / "evidence").glob("*.json"))
        self.assertTrue(ev, "실패인데 증거가 저장되지 않음")


class ProxyWiring(unittest.TestCase):
    """브라우저 프록시 배선: CPK_PROXY 파싱 + Fetch 인증/미디어 차단 이벤트 처리."""

    def setUp(self):
        import os
        self._env = {k: os.environ.get(k) for k in ("CPK_PROXY", "CPK_PROXY_USER", "CPK_PROXY_PASS")}
        for k in self._env:
            os.environ.pop(k, None)

    def tearDown(self):
        import os
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_parse_none_when_empty(self):
        self.assertIsNone(cb.proxy_config())

    def test_parse_full_url(self):
        import os
        os.environ["CPK_PROXY"] = "http://joe:secret@gw.proxy.io:8000"
        c = cb.proxy_config()
        self.assertEqual(c["server"], "http://gw.proxy.io:8000")  # server 엔 자격증명 없음
        self.assertEqual((c["user"], c["pass"]), ("joe", "secret"))

    def test_parse_hostport_with_separate_creds(self):
        import os
        os.environ["CPK_PROXY"] = "gw.proxy.io:8000"
        os.environ["CPK_PROXY_USER"] = "u1"
        os.environ["CPK_PROXY_PASS"] = "p1"
        c = cb.proxy_config()
        self.assertEqual(c["server"], "http://gw.proxy.io:8000")
        self.assertEqual((c["user"], c["pass"]), ("u1", "p1"))

    def _fake_tab(self, auth=None, block=False):
        t = cb.Tab.__new__(cb.Tab)
        t.seq = 0
        t._fire_id = 0
        t._loader = None
        t.last_status = t.last_error = None
        t._proxy_auth = auth
        t._block_media = block
        sent = []

        class WS:
            def send(self, s):
                sent.append(json.loads(s))
        t.ws = WS()
        return t, sent

    def test_auth_required_provides_credentials(self):
        t, sent = self._fake_tab(auth=("u", "p"))
        t._handle_event({"method": "Fetch.authRequired", "params": {"requestId": "R1"}})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["method"], "Fetch.continueWithAuth")
        self.assertEqual(sent[0]["params"]["authChallengeResponse"]["response"], "ProvideCredentials")
        self.assertEqual(sent[0]["params"]["authChallengeResponse"]["username"], "u")
        self.assertLess(sent[0]["id"], 0)  # 음수 id → call() seq와 충돌 없음

    def test_request_paused_continues_normal(self):
        t, sent = self._fake_tab(block=True)
        t._handle_event({"method": "Fetch.requestPaused",
                         "params": {"requestId": "R2", "resourceType": "Document"}})
        self.assertEqual(sent[0]["method"], "Fetch.continueRequest")

    def test_request_paused_blocks_media(self):
        t, sent = self._fake_tab(block=True)
        t._handle_event({"method": "Fetch.requestPaused",
                         "params": {"requestId": "R3", "resourceType": "Image"}})
        self.assertEqual(sent[0]["method"], "Fetch.failRequest")
        self.assertEqual(sent[0]["params"]["errorReason"], "BlockedByClient")

    def test_media_not_blocked_when_disabled(self):
        t, sent = self._fake_tab(block=False)
        t._handle_event({"method": "Fetch.requestPaused",
                         "params": {"requestId": "R4", "resourceType": "Image"}})
        self.assertEqual(sent[0]["method"], "Fetch.continueRequest")  # 차단 꺼지면 계속


class FreshContextPerSearch(unittest.TestCase):
    """CPK_FRESH_CONTEXT 모드: 검색 시도마다 격리 시크릿 컨텍스트를 새로 열고 반드시 닫는다.
    개인화 오염 방지(깨끗 vs 노이즈 top10 겹침 80%→50% 관측)를 코드로 고정한다."""

    def setUp(self):
        self.home = _tmp_home()
        cs.SEARCH_MIN_GAP = 0
        cs.clear_pause()
        cs.DAILY_BUDGET = 1000

    def _run_fresh(self, steps):
        state = {"i": 0, "opened": 0, "closed": 0, "warmups": 0}

        class FakeTab:
            last_status = None
            last_error = None

            def goto(self, url, settle=(0, 0)):
                if url == cs.HOME_URL:
                    state["warmups"] += 1
                    self.__class__.last_status = 200
                    self._units, self._html = 0, "<html>home</html>"
                    return
                s = steps[min(state["i"], len(steps) - 1)]
                state["i"] += 1
                self.__class__.last_status = s[0]
                self._units, self._html = s[1], s[2]

            def submit_search(self, q, settle=(0, 0)):
                s = steps[min(state["i"], len(steps) - 1)]
                state["i"] += 1
                self.__class__.last_status = s[0]
                self._units, self._html = s[1], s[2]

            def eval(self, expr):
                if "ProductUnit" in expr:
                    return getattr(self, "_units", 0)
                if "outerHTML" in expr:
                    return getattr(self, "_html", "")
                if "location" in expr:
                    return "u"
                return "t"

            def cookies(self):
                return [{"name": "_abck", "value": "x~0~y"}]  # 검증된 상태 → 워밍 즉시 통과

            def pump(self, s):
                pass

            def close(self):
                pass

        def fake_open(proxy=None):
            state["opened"] += 1

            def close_fn():
                state["closed"] += 1
            return FakeTab(), close_fn

        orig = (cb.Tab, cb.chrome_alive, cb.open_incognito_tab, cb.FRESH_CONTEXT, cb.WARM_SECS)
        cb.Tab = FakeTab
        cb.chrome_alive = lambda: True
        cb.open_incognito_tab = fake_open
        cb.FRESH_CONTEXT = True
        cb.WARM_SECS = 0  # This test covers context ownership; warmup events have separate tests.
        try:
            with cb.search_session("test", warmup=False) as fetch:
                r = fetch("q", 1)
        finally:
            cb.Tab, cb.chrome_alive, cb.open_incognito_tab, cb.FRESH_CONTEXT, cb.WARM_SECS = orig
        return r, state

    def test_fresh_context_opened_and_closed_once(self):
        r, st = self._run_fresh([(200, 60, "<html>ok</html>")])
        self.assertEqual(r["outcome"], "ok")
        self.assertEqual(st["opened"], 1)
        self.assertEqual(st["closed"], 1)  # 반드시 정리
        self.assertEqual(st["warmups"], 1)  # cold 컨텍스트는 홈 워밍업

    def test_context_closed_on_each_retry(self):
        r, st = self._run_fresh([(200, 0, "<div class='sec-if-cpt-container'></div>"),
                                 (200, 60, "<html>ok</html>")])
        self.assertEqual(r["outcome"], "ok")
        self.assertEqual(st["opened"], 2)
        self.assertEqual(st["closed"], 2)  # 시도마다 열고 닫음(컨텍스트 누수 없음)


class DailyBudget(unittest.TestCase):
    """하루 상한을 넘기면 그날은 더 요청하지 않아야 한다."""

    def setUp(self):
        self.home = _tmp_home()
        cs.JAR.write_text('[{"name":"PCID","value":"x","domain":".coupang.com","path":"/","expires":null,"secure":true}]',
                          encoding="utf-8")
        cc.DATA = Path(tempfile.mkdtemp(prefix="cpk_data_"))
        cc.STATE = self.home / "collect.json"
        kwf = self.home / "keywords.txt"
        kwf.write_text("a\nb\nc\n", encoding="utf-8")
        cc.KEYWORDS = kwf
        cc.QUIET = ""
        cc.SYNC_TARGET = ""
        cc.MODE = "python"
        cs.SEARCH_MIN_GAP = 0
        cc.GAP_MIN = cc.GAP_MAX = 0.0
        cc.DAILY_BUDGET = 1
        cs.DAILY_BUDGET = 1

    def test_stops_at_budget(self):
        fixture = (Path(__file__).resolve().parent / "fixtures" / "search_sample.html").read_text(encoding="utf-8")
        n = {"c": 0}

        def fake_search_html(s, q, page=1):
            n["c"] += 1
            return 200, fixture

        orig = cs.search_html, cs.autocomplete
        cs.search_html = fake_search_html
        cs.autocomplete = lambda s, q, referer=None: []
        try:
            cc.run()
        finally:
            cs.search_html, cs.autocomplete = orig
        self.assertEqual(n["c"], 1, f"상한 1인데 {n['c']}회 요청함")


class BackoffSkips(unittest.TestCase):
    """backoff_until 이 미래면 수집을 건너뛴다."""

    def setUp(self):
        self.home = _tmp_home()
        cs.JAR.write_text('[{"name":"PCID","value":"x"}]', encoding="utf-8")
        cc.DATA = Path(tempfile.mkdtemp(prefix="cpk_data_"))
        cc.STATE = self.home / "collect.json"
        kwf = self.home / "keywords.txt"
        kwf.write_text("a\n", encoding="utf-8")
        cc.KEYWORDS = kwf
        cc.QUIET = ""
        cc.MODE = "python"
        cs.SEARCH_MIN_GAP = 0
        cs.pause(3600, reason="test")

    def test_backoff_returns_4(self):
        called = {"c": 0}
        cs.search_html = lambda *a, **k: called.__setitem__("c", called["c"] + 1) or (200, "")
        rc = cc.run()
        self.assertEqual(rc, 4)
        self.assertEqual(called["c"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
