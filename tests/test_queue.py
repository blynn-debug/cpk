"""작업 큐·재개 오프라인 테스트 — 네트워크 없음(가짜 fetch)."""
import json
import tempfile
import unittest
from pathlib import Path

import cpk_session as cs
import cpk_collect as cc
import cpk_browser as cb
import cpk_queue as q


def _tmp_home():
    d = Path(tempfile.mkdtemp(prefix="cpk_q_"))
    cs.HOME = d
    cs.JAR = d / "jar.json"
    cs.HEALTH = d / "health.json"
    cs.LOG = d / "keepalive.log"
    return d


FIX = Path(__file__).resolve().parent / "fixtures" / "search_sample.html"


class Queue(unittest.TestCase):
    def setUp(self):
        self.home = _tmp_home()
        cs.SEARCH_MIN_GAP = 0
        cs.clear_pause()
        cs.DAILY_BUDGET = 1000
        cc.DATA = Path(tempfile.mkdtemp(prefix="cpk_qdata_"))
        cc.SYNC_TARGET = ""
        cc.GAP_MIN = cc.GAP_MAX = 0.0
        q._qpath  # ensure module uses cs.HOME dynamically
        self.html = FIX.read_text(encoding="utf-8")

    def _fake_session(self, script):
        """script: query -> outcome. search_session 컨텍스트를 흉내내는 fetch 주입."""
        import contextlib
        html = self.html

        @contextlib.contextmanager
        def fake_search_session(kind="ondemand", warmup=True):
            def fetch(query, page=1, tries=3):
                oc = script(query)
                return {"outcome": oc, "http_status": 200 if oc in ("ok", "no_results") else 403,
                        "html": html if oc == "ok" else "", "product_count": 60 if oc == "ok" else 0,
                        "final_url": "u", "title": "t", "error": None}
            yield fetch
        return fake_search_session

    def test_enqueue_and_full_run(self):
        q.enqueue([("a", 1), ("b", 1)])
        orig_ss, orig_av, orig_ac = cb.search_session, cb.available, cs.autocomplete_keywords
        cb.available = lambda: True
        cb.search_session = self._fake_session(lambda kw: "ok")
        cs.autocomplete_keywords = lambda kw, s=None: {"ok": True, "items": [], "used": kw}
        try:
            rc = q.run_queue()
        finally:
            cb.search_session, cb.available, cs.autocomplete_keywords = orig_ss, orig_av, orig_ac
        self.assertEqual(rc, 0)
        st = q.status()
        self.assertEqual(st["ok"], 2)
        self.assertEqual(st["pending"], 0)

    def test_stop_on_block_keeps_pending_and_resumes(self):
        q.enqueue([("a", 1), ("b", 1), ("c", 1)])
        # 'b'에서 차단 → 멈춤, b·c pending 유지
        seq = {"n": 0}

        def script(kw):
            return "challenge" if kw == "b" else "ok"

        orig_ss, orig_av, orig_ac = cb.search_session, cb.available, cs.autocomplete_keywords
        cb.available = lambda: True
        cb.search_session = self._fake_session(script)
        cs.autocomplete_keywords = lambda kw, s=None: {"ok": True, "items": [], "used": kw}
        try:
            q.run_queue()
            st1 = q.status()
            self.assertEqual(st1["ok"], 1)      # a 완료
            self.assertEqual(st1["pending"], 2)  # b,c 재개 대상
            # 이제 차단 해제 상태로 재개 → 나머지 완료
            cs.clear_pause()
            cb.search_session = self._fake_session(lambda kw: "ok")
            q.run_queue()
            st2 = q.status()
            self.assertEqual(st2["pending"], 0)
            self.assertEqual(st2["ok"], 3)
        finally:
            cb.search_session, cb.available, cs.autocomplete_keywords = orig_ss, orig_av, orig_ac

    def test_reset(self):
        q.enqueue([("a", 1)])
        q._save({})
        self.assertEqual(q.status()["total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
