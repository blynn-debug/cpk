import json, unittest
import cpk_market as m

RESULT = {"count": 4, "total_count": 1200,
          "badge_counts": {"로켓": 2, "판매자로켓": 1, "일반": 1},
          "ads": 1,
          "items": [
              {"price": 3000, "reviews": 100, "badge": "로켓"},
              {"price": 7000, "reviews": 900, "badge": "로켓"},
              {"price": 5000, "reviews": 0, "badge": "판매자로켓"},
              {"price": 22000, "reviews": 400, "badge": "일반"},
          ]}

class SnapshotFromResult(unittest.TestCase):
    def test_counts_and_badges(self):
        s = m.snapshot_from_result(RESULT)
        self.assertEqual(s["result_count"], 1200)
        self.assertEqual(s["units_shown"], 4)
        self.assertEqual(s["rocket_cnt"], 2)
        self.assertEqual(s["seller_rocket_cnt"], 1)
        self.assertEqual(s["general_cnt"], 1)
        self.assertEqual(s["ad_cnt"], 1)

    def test_reviews_and_prices(self):
        s = m.snapshot_from_result(RESULT)
        self.assertEqual(s["review_sum"], 1400)
        self.assertEqual(s["review_max"], 900)
        self.assertEqual(s["price_min"], 3000)
        self.assertEqual(s["price_max"], 22000)
        self.assertEqual(s["price_med"], 6000)  # (5000+7000)/2 중앙값
        json.loads(s["top_json"])  # 유효 JSON

    def test_empty_items(self):
        s = m.snapshot_from_result({"count": 0, "total_count": 0, "badge_counts": {}, "ads": 0, "items": []})
        self.assertEqual(s["review_sum"], 0)
        self.assertIsNone(s["price_min"])

    def test_related_and_autocomplete_preserved(self):
        r = {**RESULT, "related_keywords": ["연관1", "연관2"], "autocomplete": ["자동1", "자동2", "자동3"]}
        s = m.snapshot_from_result(r)
        self.assertEqual(json.loads(s["related_json"]), ["연관1", "연관2"])
        self.assertEqual(json.loads(s["auto_json"]), ["자동1", "자동2", "자동3"])

    def test_related_missing_defaults_empty(self):
        s = m.snapshot_from_result({"count": 0, "total_count": 0, "badge_counts": {}, "ads": 0, "items": []})
        self.assertEqual(json.loads(s["related_json"]), [])
        self.assertEqual(json.loads(s["auto_json"]), [])
