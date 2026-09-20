import unittest
import cpk_market as m

def rel(kw): return {"계란보관함": ["계란트레이", "계란보관함"], "수세미": ["수세미걸이"]}.get(kw, [])
def auto(kw): return {"계란보관함": ["계란보관함 30구", "계란트레이"]}.get(kw, [])

class ExpandSeeds(unittest.TestCase):
    def test_includes_seed_and_dedups(self):
        out = m.expand_seeds(["계란보관함"], rel, auto)
        kws = [o["keyword"] for o in out]
        self.assertIn("계란보관함", kws)
        self.assertEqual(len(kws), len(set(kws)))               # 중복 없음
        self.assertIn("계란트레이", kws)                         # related/auto 병합
        self.assertEqual([o for o in out if o["keyword"] == "계란보관함"][0]["source"], "seed")
        self.assertEqual([o for o in out if o["keyword"] == "계란트레이"][0]["parent"], "계란보관함")

    def test_cap_limits_total(self):
        out = m.expand_seeds(["계란보관함", "수세미"], rel, auto, cap=3)
        self.assertLessEqual(len(out), 3)

    def test_expander_error_is_skipped(self):
        def boom(kw): raise RuntimeError("x")
        out = m.expand_seeds(["수세미"], boom, auto)   # related 실패해도 seed는 남음
        self.assertIn("수세미", [o["keyword"] for o in out])
