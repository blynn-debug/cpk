import unittest
import cpk_market_db as mdb
import cpk_market_seeds as ms

def rel(kw): return {"계란보관함": ["계란트레이", "계란보관함"]}.get(kw, [])
def auto(kw): return {"계란보관함": ["계란보관함 30구"]}.get(kw, [])

class Seeds(unittest.TestCase):
    def setUp(self):
        self.conn = mdb.connect(":memory:")

    def test_expand_and_store_counts_new(self):
        n = ms.expand_and_store(self.conn, ["계란보관함"], rel, auto)
        self.assertGreaterEqual(n, 3)  # seed + 계란트레이 + 계란보관함 30구
        kws = {c["keyword"] for c in ms.candidates(self.conn)}
        self.assertIn("계란보관함", kws)
        self.assertIn("계란트레이", kws)

    def test_reexpand_adds_no_duplicates(self):
        ms.expand_and_store(self.conn, ["계란보관함"], rel, auto)
        n2 = ms.expand_and_store(self.conn, ["계란보관함"], rel, auto)
        self.assertEqual(n2, 0)  # 이미 다 있음

    def test_promote_and_archive(self):
        ms.expand_and_store(self.conn, ["계란보관함"], rel, auto)
        self.assertTrue(ms.promote(self.conn, "계란트레이"))
        self.assertEqual([r["keyword"] for r in mdb.tracked_keywords(self.conn)], ["계란트레이"])
        self.assertFalse(ms.promote(self.conn, "없는키워드"))
        self.assertTrue(ms.archive(self.conn, "계란트레이"))
        self.assertEqual(mdb.tracked_keywords(self.conn), [])

    def test_candidates_only_candidate_status(self):
        ms.expand_and_store(self.conn, ["계란보관함"], rel, auto)
        ms.promote(self.conn, "계란보관함")
        kws = {c["keyword"] for c in ms.candidates(self.conn)}
        self.assertNotIn("계란보관함", kws)  # 승격되면 후보에서 빠짐
