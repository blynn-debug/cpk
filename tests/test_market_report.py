import unittest
import cpk_market_db as mdb
import cpk_market_report as mr

SNAP = {"result_count": 500, "units_shown": 60, "rocket_cnt": 12, "seller_rocket_cnt": 6,
        "general_cnt": 42, "ad_cnt": 3, "review_sum": 1000, "review_max": 300,
        "price_min": 3000, "price_med": 7000, "price_max": 22000, "top_json": "[]"}

class Report(unittest.TestCase):
    def setUp(self):
        self.conn = mdb.connect(":memory:")
        a = mdb.upsert_keyword(self.conn, "계란보관함", "seed", status="tracked")
        b = mdb.upsert_keyword(self.conn, "무타공선반", "seed", status="tracked")
        mdb.insert_snapshot(self.conn, a, SNAP, day="2026-09-20")
        mdb.insert_snapshot(self.conn, a, {**SNAP, "review_sum": 1200}, day="2026-09-21")
        mdb.insert_snapshot(self.conn, b, {**SNAP, "review_sum": 50}, day="2026-09-21")
        mdb.save_scores(self.conn, a, "2026-09-21", {"rarity":0.7,"demand":0.6,"steadiness":0.5,"opportunity":0.6})
        mdb.save_scores(self.conn, b, "2026-09-21", {"rarity":0.4,"demand":0.2,"steadiness":None,"opportunity":0.3})
        mdb.save_sourcing(self.conn, a, {"market":"dome","exists":True,"count":42})

    def test_sorted_by_opportunity_desc(self):
        r = mr.report(self.conn)
        self.assertEqual([m["keyword"] for m in r["markets"]], ["계란보관함", "무타공선반"])
        self.assertIn("generated", r)

    def test_latest_and_trend(self):
        r = mr.report(self.conn)
        egg = r["markets"][0]
        self.assertEqual(egg["latest"]["review_sum"], 1200)     # 최신 스냅샷
        self.assertEqual(len(egg["trend"]), 2)                  # 2일 추이
        self.assertEqual(egg["trend"][0]["day"], "2026-09-20")
        self.assertAlmostEqual(egg["trend"][0]["rocket_ratio"], 18/60)  # (12+6)/60
        self.assertEqual(egg["dome_exists"], 1)
        self.assertEqual(egg["dome_count"], 42)

    def test_none_opportunity_sorts_last(self):
        c = mdb.upsert_keyword(self.conn, "무점수", "seed", status="tracked")
        mdb.insert_snapshot(self.conn, c, SNAP, day="2026-09-21")  # 점수 없음
        r = mr.report(self.conn)
        self.assertEqual(r["markets"][-1]["keyword"], "무점수")

    def test_related_and_autocomplete_full_lists(self):
        e = mdb.upsert_keyword(self.conn, "연관키", "seed", status="tracked")
        snap = {**SNAP, "related_json": '["계란트레이","계란정리함","달걀보관"]',
                "auto_json": '["계란보관함 30구","계란보관함 대형"]'}
        mdb.insert_snapshot(self.conn, e, snap, day="2026-09-21")
        r = mr.report(self.conn)
        row = next(m for m in r["markets"] if m["keyword"] == "연관키")
        self.assertEqual(row["related"], ["계란트레이", "계란정리함", "달걀보관"])
        self.assertEqual(row["autocomplete"], ["계란보관함 30구", "계란보관함 대형"])

    def test_related_missing_is_empty_list(self):
        r = mr.report(self.conn)  # setUp 스냅샷엔 related_json 없음
        self.assertEqual(r["markets"][0]["related"], [])
        self.assertEqual(r["markets"][0]["autocomplete"], [])

    def test_sample_flag_from_source(self):
        s = mdb.upsert_keyword(self.conn, "샘플키", "sample", status="tracked")
        mdb.insert_snapshot(self.conn, s, SNAP, day="2026-09-21")
        r = mr.report(self.conn)
        row = next(m for m in r["markets"] if m["keyword"] == "샘플키")
        self.assertTrue(row["sample"])
        self.assertEqual(row["source"], "sample")
        # 일반 소스는 sample=False
        other = next(m for m in r["markets"] if m["keyword"] == "계란보관함")
        self.assertFalse(other["sample"])

    def test_null_rocket_counts_no_crash(self):
        # rocket_cnt/seller_rocket_cnt 가 NULL 이어도 units>0 이면 리포트가 죽지 않는다.
        d = mdb.upsert_keyword(self.conn, "널로켓", "seed", status="tracked")
        snap = {**SNAP, "rocket_cnt": None, "seller_rocket_cnt": None}
        mdb.insert_snapshot(self.conn, d, snap, day="2026-09-21")
        r = mr.report(self.conn)  # 예외 없어야 함
        row = next(m for m in r["markets"] if m["keyword"] == "널로켓")
        self.assertEqual(row["trend"][0]["rocket_ratio"], 0.0)
