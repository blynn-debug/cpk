import unittest
import cpk_market_score as sc

def snap(day, rc=1000, units=60, rocket=6, ad=3, review=1000):
    return {"day": day, "result_count": rc, "units_shown": units, "rocket_cnt": rocket,
            "seller_rocket_cnt": 0, "general_cnt": units - rocket, "ad_cnt": ad,
            "review_sum": review, "review_max": review}

class Rarity(unittest.TestCase):
    def test_rarer_market_scores_higher(self):
        rare = sc.rarity(snap("d", rc=200, rocket=2, ad=0))
        crowded = sc.rarity(snap("d", rc=40000, rocket=55, ad=10))
        self.assertGreater(rare, crowded)
        self.assertTrue(0.0 <= rare <= 1.0 and 0.0 <= crowded <= 1.0)

class Demand(unittest.TestCase):
    def test_growing_reviews_beats_flat(self):
        growing = sc.demand([snap("2026-09-15", review=100), snap("2026-09-21", review=700)])
        flat = sc.demand([snap("2026-09-15", review=100), snap("2026-09-21", review=100)])
        self.assertGreater(growing, flat)

    def test_single_snapshot_uses_level_only(self):
        d = sc.demand([snap("2026-09-21", review=100)])
        self.assertTrue(0.0 <= d <= 1.0)

class Steadiness(unittest.TestCase):
    def test_none_when_too_few(self):
        self.assertIsNone(sc.steadiness([snap("2026-09-20"), snap("2026-09-21")]))

    def test_steady_positive_beats_erratic(self):
        steady = sc.steadiness([snap(f"2026-09-1{i}", review=100 + 20 * i) for i in range(5)])
        erratic = sc.steadiness([snap("2026-09-10", review=100), snap("2026-09-11", review=100),
                                 snap("2026-09-12", review=900), snap("2026-09-13", review=900),
                                 snap("2026-09-14", review=905)])
        self.assertIsNotNone(steady)
        self.assertGreater(steady, erratic)

class Opportunity(unittest.TestCase):
    def test_returns_subscores_and_composite(self):
        o = sc.opportunity([snap(f"2026-09-1{i}", review=100 + 20 * i) for i in range(5)])
        for k in ("rarity", "demand", "steadiness", "opportunity"):
            self.assertIn(k, o)
        self.assertTrue(0.0 <= o["opportunity"] <= 1.0)

    def test_opportunity_skips_none_steadiness(self):
        o = sc.opportunity([snap("2026-09-21", review=100)])  # 1개 → steadiness None
        self.assertIsNone(o["steadiness"])
        self.assertTrue(0.0 <= o["opportunity"] <= 1.0)
