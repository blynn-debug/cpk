"""유닛 테스트 — 네트워크 없음. 파서·쿠키·상태 로직의 핵심 기능만 검증.
실행: python -m unittest tests.test_unit  (cpk 디렉터리에서)
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"

import cpk_session as cs
import cpk_search as sr
import cpk_collect as cc


def _tmp_home() -> Path:
    d = Path(tempfile.mkdtemp(prefix="cpk_test_"))
    cs.HOME = d
    cs.JAR = d / "jar.json"
    cs.HEALTH = d / "health.json"
    cs.LOG = d / "keepalive.log"
    return d


class Parse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (FIX / "search_sample.html").read_text(encoding="utf-8")
        cls.exp = json.loads((FIX / "search_sample.expected.json").read_text(encoding="utf-8"))
        cls.rows = sr.parse(cls.html)

    def test_count_60(self):
        self.assertEqual(len(self.rows), self.exp["count"])
        self.assertEqual(len(self.rows), 60)

    def test_every_item_has_price_and_id(self):
        self.assertTrue(all(r["price"] for r in self.rows), "가격 없는 항목")
        self.assertTrue(all(r["product_id"] for r in self.rows), "상품번호 없는 항목")

    def test_badge_distribution_matches(self):
        counts = {}
        for r in self.rows:
            counts[r["badge"]] = counts.get(r["badge"], 0) + 1
        self.assertEqual(counts, self.exp["badges"])

    def test_badges_are_known_values(self):
        known = {"로켓", "판매자로켓", "로켓와우", "로켓(도착보장)", "일반"}
        self.assertTrue(set(r["badge"] for r in self.rows) <= known)

    def test_ads_flagged(self):
        self.assertEqual(sum(r["ad"] for r in self.rows), self.exp["ads"])
        self.assertGreater(self.exp["ads"], 0)

    def test_related_keywords(self):
        self.assertEqual(sr.parse_related_keywords(self.html), self.exp["related"])
        self.assertEqual(len(self.exp["related"]), 10)

    def test_total_count(self):
        self.assertEqual(sr.parse_total_count(self.html), self.exp["total"])

    def test_units_in_matches_parse(self):
        self.assertEqual(cs.units_in(self.html), len(self.rows))

    def test_challenge_page_detected(self):
        self.assertTrue(cs.is_challenge('<div class="sec-if-cpt-container"></div>'))
        self.assertFalse(cs.is_challenge(self.html))


class Cookies(unittest.TestCase):
    def setUp(self):
        _tmp_home()

    def test_import_strips_login_cookies(self):
        header = "PCID=abc; member_srl=123; ILOGIN=Y; CT_AT=tok; bm_sz=z; _abck=a"
        n = cs.import_cookie_header(header)
        names = {c["name"] for c in cs.load_jar()}
        self.assertNotIn("member_srl", names)
        self.assertNotIn("ILOGIN", names)
        self.assertNotIn("CT_AT", names)
        self.assertEqual(names, {"PCID", "bm_sz", "_abck"})
        self.assertEqual(n, 3)

    def test_import_keeps_login_when_asked(self):
        cs.import_cookie_header("PCID=abc; ILOGIN=Y", keep_login=True)
        self.assertIn("ILOGIN", {c["name"] for c in cs.load_jar()})

    def test_import_strips_cookie_prefix(self):
        cs.import_cookie_header("cookie: PCID=abc; bm_s=x")
        self.assertEqual({c["name"] for c in cs.load_jar()}, {"PCID", "bm_s"})

    def test_import_empty_raises(self):
        with self.assertRaises(ValueError):
            cs.import_cookie_header("   ")

    def test_roundtrip_jar(self):
        cs.import_cookie_header("PCID=abc; bm_sz=zzz")
        self.assertEqual({c["name"]: c["value"] for c in cs.load_jar()},
                         {"PCID": "abc", "bm_sz": "zzz"})

    def test_ua_override_reads_file(self):
        cs.reset_ua_override()
        self.assertEqual(cs.ua_override(), {})
        (cs.HOME / "ua.json").write_text('{"user-agent":"UA-X","sec-ch-ua-platform":"\\"macOS\\""}',
                                         encoding="utf-8")
        cs.reset_ua_override()
        self.assertEqual(cs.ua_override()["user-agent"], "UA-X")


class Health(unittest.TestCase):
    def setUp(self):
        _tmp_home()

    def test_health_roundtrip(self):
        cs.write_health({"ok": True, "runs": 3, "fails": 0})
        h = cs.read_health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["runs"], 3)
        self.assertIn("ts", h)

    def test_read_health_missing_is_empty(self):
        self.assertEqual(cs.read_health(), {})


class CollectHelpers(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(cc.safe_name("방충망 잠금장치"), "방충망_잠금장치")
        self.assertEqual(cc.safe_name("2단 선반"), "2단_선반")
        self.assertTrue(cc.safe_name("///").startswith("q") or cc.safe_name("///"))

    def test_quiet_hours(self):
        import datetime as dt
        cc.QUIET = "02-06"
        self.assertTrue(cc.in_quiet_hours(dt.datetime(2026, 1, 1, 3)))
        self.assertFalse(cc.in_quiet_hours(dt.datetime(2026, 1, 1, 10)))
        cc.QUIET = "22-06"  # 자정 넘김
        self.assertTrue(cc.in_quiet_hours(dt.datetime(2026, 1, 1, 23)))
        self.assertTrue(cc.in_quiet_hours(dt.datetime(2026, 1, 1, 5)))
        self.assertFalse(cc.in_quiet_hours(dt.datetime(2026, 1, 1, 12)))
        cc.QUIET = ""
        self.assertFalse(cc.in_quiet_hours(dt.datetime(2026, 1, 1, 3)))

    def test_load_keywords_parses_pages_and_comments(self):
        d = Path(tempfile.mkdtemp(prefix="cpk_kw_"))
        f = d / "keywords.txt"
        f.write_text("# 주석\n방충망 잠금장치\n2단 선반 | pages=3\n\n  # 빈줄/주석\n계란보관함|pages=9\n",
                     encoding="utf-8")
        cc.KEYWORDS = f
        kws = cc.load_keywords()
        self.assertEqual(kws[0], ("방충망 잠금장치", 1))
        self.assertEqual(kws[1], ("2단 선반", 3))
        self.assertEqual(kws[2], ("계란보관함", 5))  # 5로 상한
        self.assertEqual(len(kws), 3)

class SearchGate(unittest.TestCase):
    def test_enforces_min_gap(self):
        import time
        _tmp_home()
        with cs.search_gate(min_gap=0):
            pass
        t0 = time.time()
        with cs.search_gate(min_gap=1.0):  # 직전 검색 직후 → 최소 간격만큼 대기해야
            pass
        self.assertGreaterEqual(time.time() - t0, 0.9)


class AutocompleteKeywords(unittest.TestCase):
    """공용 자동완성 헬퍼: 축약 재시도, 조회 실패와 빈 결과 구분."""

    def setUp(self):
        _tmp_home()
        cs.SEARCH_MIN_GAP = 0
        cs.clear_pause()

    def test_shortens_long_query(self):
        def fake_ac(s, q, referer=None):
            hit = [{"keyword": "x", "travel": False}] if q == "방지 테이프" else []
            return {"items": hit, "status": 200, "ok": True}
        orig = cs.autocomplete
        cs.autocomplete = fake_ac
        try:
            r = cs.autocomplete_keywords("틈새 곰팡이 방지 테이프")
        finally:
            cs.autocomplete = orig
        self.assertTrue(r["ok"])
        self.assertEqual(r["items"], ["x"])
        self.assertEqual(r["used"], "방지 테이프")

    def test_failure_flagged(self):
        def boom(s, q, referer=None):
            raise RuntimeError("net")
        orig = cs.autocomplete
        cs.autocomplete = boom
        try:
            r = cs.autocomplete_keywords("계란보관함")
        finally:
            cs.autocomplete = orig
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"], [])

    def test_empty_is_ok_not_failure(self):
        orig = cs.autocomplete
        cs.autocomplete = lambda s, q, referer=None: {"items": [], "status": 200, "ok": True}
        try:
            r = cs.autocomplete_keywords("계란보관함")
        finally:
            cs.autocomplete = orig
        self.assertTrue(r["ok"])  # 빈 결과는 조회 실패가 아님
        self.assertEqual(r["items"], [])


class StoreAtomicity(unittest.TestCase):
    """저장 원자성: 임시 파일이 남지 않고, append가 줄 단위로 쌓인다."""

    def setUp(self):
        _tmp_home()

    def test_atomic_write_no_tmp_left(self):
        p = cs.HOME / "x.json"
        cs.atomic_write(p, '{"a": 1}')
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1})
        self.assertFalse((cs.HOME / "x.json.tmp").exists())

    def test_atomic_write_overwrite(self):
        p = cs.HOME / "x.json"
        cs.atomic_write(p, "old")
        cs.atomic_write(p, "new")
        self.assertEqual(p.read_text(encoding="utf-8"), "new")

    def test_append_line(self):
        p = cs.HOME / "r.jsonl"
        cs.append_line(p, "a")
        cs.append_line(p, "b")
        self.assertEqual(p.read_text(encoding="utf-8").splitlines(), ["a", "b"])


class RequestControl(unittest.TestCase):
    """공통 요청 제어: 유형별 집계, 중단 상태, 예산 초과."""

    def setUp(self):
        _tmp_home()
        cs.SEARCH_MIN_GAP = 0
        cs.clear_pause()

    def test_admit_request_counts_by_kind(self):
        cs.admit_request("collect")
        cs.admit_request("collect")
        total = cs.admit_request("health")
        self.assertEqual(total, 3)
        self.assertEqual(cs.read_control()["counts"], {"collect": 2, "health": 1})

    def test_budget_not_counted_when_exceeded(self):
        old = cs.DAILY_BUDGET
        cs.DAILY_BUDGET = 1
        try:
            with cs.request_gate("collect"):
                pass
            with self.assertRaises(cs.BudgetExceeded):
                with cs.request_gate("collect"):
                    pass
        finally:
            cs.DAILY_BUDGET = old
        # 보내지 않은(예산 초과) 요청은 집계되지 않는다
        self.assertEqual(sum(cs.read_control()["counts"].values()), 1)

    def test_pause_and_clear(self):
        self.assertEqual(cs.paused_remaining(), 0)
        cs.pause(100, "test")
        self.assertGreater(cs.paused_remaining(), 90)
        cs.clear_pause()
        self.assertEqual(cs.paused_remaining(), 0)

    def test_gate_raises_when_paused(self):
        cs.pause(100, "test")
        with self.assertRaises(cs.RequestPaused):
            with cs.request_gate("collect"):
                pass

    def test_gate_budget_exceeded(self):
        old = cs.DAILY_BUDGET
        cs.DAILY_BUDGET = 2
        try:
            with cs.request_gate("collect"):
                pass
            with cs.request_gate("collect"):
                pass
            with self.assertRaises(cs.BudgetExceeded):
                with cs.request_gate("collect"):
                    pass
        finally:
            cs.DAILY_BUDGET = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
