import unittest
import cpk_market_db as mdb
import cpk_market_collect as mc

RESULT_OK = {"count": 2, "total_count": 500, "badge_counts": {"로켓": 1, "일반": 1}, "ads": 0,
             "items": [{"price": 3000, "reviews": 100, "badge": "로켓"},
                       {"price": 5000, "reviews": 300, "badge": "일반"}]}

class RunCollection(unittest.TestCase):
    def setUp(self):
        self.conn = mdb.connect(":memory:")
        for kw in ("계란보관함", "수세미걸이"):
            mdb.upsert_keyword(self.conn, kw, "seed", status="tracked")

    def test_collects_snapshot_sourcing_scores(self):
        def search_fn(kw): return {**RESULT_OK, "outcome": "ok"}
        def sourcing_fn(kw): return {"market": "dome", "exists": True, "count": 12}
        kws = mdb.tracked_keywords(self.conn)
        r = mc.run_collection(self.conn, kws, search_fn, sourcing_fn, today="2026-09-21")
        self.assertEqual(r["collected"], 2)
        rows = mdb.tracked_keywords(self.conn)
        self.assertTrue(all(row["opportunity"] is not None for row in rows))
        self.assertTrue(all(row["dome_exists"] == 1 for row in rows))

    def test_blocked_search_is_failed_not_crash(self):
        def search_fn(kw): return {"outcome": "challenge", "items": []}
        def sourcing_fn(kw): return {"market": "dome", "exists": False, "count": 0}
        kws = mdb.tracked_keywords(self.conn)
        r = mc.run_collection(self.conn, kws, search_fn, sourcing_fn, today="2026-09-21")
        self.assertEqual(r["collected"], 0)
        self.assertEqual(r["failed"], 2)

    def test_sourcing_error_does_not_block_snapshot(self):
        def search_fn(kw): return {**RESULT_OK, "outcome": "ok"}
        def sourcing_fn(kw): raise RuntimeError("api down")
        kws = mdb.tracked_keywords(self.conn)
        r = mc.run_collection(self.conn, kws, search_fn, sourcing_fn, today="2026-09-21")
        self.assertEqual(r["collected"], 2)   # 스냅샷·점수는 저장, 소싱만 생략
