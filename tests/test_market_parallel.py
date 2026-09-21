import unittest
import cpk_market_db as mdb
import cpk_market_parallel as mp

OK = {"result_count": 500, "count": 4, "total_count": 500,
      "badge_counts": {"로켓": 1, "일반": 3}, "ads": 0,
      "related_keywords": ["연관1"], "autocomplete": ["자동1"],
      "outcome": "ok",
      "items": [{"price": 3000, "reviews": 100, "badge": "로켓", "name": "a"},
                {"price": 7000, "reviews": 900, "badge": "일반", "name": "b"}]}
BLOCKED = {"outcome": "challenge", "items": [], "badge_counts": {}, "related_keywords": [],
           "autocomplete": [], "count": 0, "total_count": None, "ads": 0}


class CollectParallel(unittest.TestCase):
    def setUp(self):
        self.conn = mdb.connect(":memory:")

    def _fake_many(self, results):
        def _f(queries, workers=10, tries=2):
            return {q: results.get(q, dict(BLOCKED)) for q in queries}
        return _f

    def test_stores_ok_skips_failures(self):
        results = {"계란트레이": dict(OK), "차단키": dict(BLOCKED)}
        r = mp.collect_parallel(self.conn, ["계란트레이", "차단키"],
                                lambda kw: {"market": "dome", "exists": True, "count": 5},
                                "2026-09-21", search_many_fn=self._fake_many(results))
        self.assertEqual(r["collected"], 1)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["detail"]["차단키"], "challenge")
        tracked = [t["keyword"] for t in mdb.tracked_keywords(self.conn)]
        self.assertIn("계란트레이", tracked)
        self.assertNotIn("차단키", tracked)

    def test_stored_snapshot_has_scores_and_sourcing(self):
        r = mp.collect_parallel(self.conn, ["계란트레이"],
                                lambda kw: {"market": "dome", "exists": True, "count": 7},
                                "2026-09-21", search_many_fn=self._fake_many({"계란트레이": dict(OK)}))
        self.assertEqual(r["collected"], 1)
        row = mdb.tracked_keywords(self.conn)[0]
        self.assertEqual(row["dome_count"], 7)
        self.assertIsNotNone(row["opportunity"])

    def test_search_many_dedupes_and_caps_workers(self):
        seen = []
        def fake_fetch(q, tries):
            seen.append(q)
            return dict(OK)
        out = mp.search_many(["a", "a", "b", ""], workers=10, fetch_fn=fake_fetch)
        self.assertEqual(sorted(out.keys()), ["a", "b"])  # 중복·공백 제거
        self.assertEqual(sorted(seen), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
