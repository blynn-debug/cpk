# tests/test_market_db.py
import unittest
import cpk_market_db as mdb

SNAP = {"result_count": 1200, "units_shown": 60, "rocket_cnt": 12, "seller_rocket_cnt": 8,
        "general_cnt": 40, "ad_cnt": 6, "review_sum": 3400, "review_max": 900,
        "price_min": 3000, "price_med": 7000, "price_max": 22000, "top_json": "[]"}

class MarketDb(unittest.TestCase):
    def setUp(self):
        self.conn = mdb.connect(":memory:")

    def test_upsert_keyword_idempotent(self):
        a = mdb.upsert_keyword(self.conn, "계란보관함", "seed", status="tracked")
        b = mdb.upsert_keyword(self.conn, "계란보관함", "related")  # 같은 키워드 재삽입
        self.assertEqual(a, b)  # 같은 id
        rows = mdb.tracked_keywords(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "tracked")

    def test_status_filter(self):
        k1 = mdb.upsert_keyword(self.conn, "a", "seed", status="tracked")
        mdb.upsert_keyword(self.conn, "b", "seed", status="candidate")
        self.assertEqual([r["keyword"] for r in mdb.tracked_keywords(self.conn)], ["a"])
        mdb.set_status(self.conn, k1, "archived")
        self.assertEqual(mdb.tracked_keywords(self.conn), [])

    def test_snapshot_roundtrip_ordered(self):
        k = mdb.upsert_keyword(self.conn, "a", "seed", status="tracked")
        mdb.insert_snapshot(self.conn, k, SNAP, day="2026-09-20")
        mdb.insert_snapshot(self.conn, k, {**SNAP, "review_sum": 3500}, day="2026-09-21")
        snaps = mdb.snapshots_for(self.conn, k)
        self.assertEqual([s["day"] for s in snaps], ["2026-09-20", "2026-09-21"])
        self.assertEqual(snaps[1]["review_sum"], 3500)

    def test_snapshot_same_day_upserts(self):
        k = mdb.upsert_keyword(self.conn, "a", "seed", status="tracked")
        mdb.insert_snapshot(self.conn, k, SNAP, day="2026-09-21")
        mdb.insert_snapshot(self.conn, k, {**SNAP, "review_sum": 9999}, day="2026-09-21")
        snaps = mdb.snapshots_for(self.conn, k)
        self.assertEqual(len(snaps), 1)          # 하루 1행
        self.assertEqual(snaps[0]["review_sum"], 9999)

    def test_sourcing_and_scores(self):
        k = mdb.upsert_keyword(self.conn, "a", "seed", status="tracked")
        mdb.save_sourcing(self.conn, k, {"market": "dome", "exists": True, "count": 42})
        mdb.save_scores(self.conn, k, "2026-09-21",
                        {"rarity": 0.7, "demand": 0.5, "steadiness": None, "opportunity": 0.6})
        rows = mdb.tracked_keywords(self.conn)
        self.assertEqual(rows[0]["dome_count"], 42)
        self.assertEqual(rows[0]["opportunity"], 0.6)

    def test_related_auto_json_roundtrip(self):
        k = mdb.upsert_keyword(self.conn, "a", "seed", status="tracked")
        snap = {**SNAP, "related_json": '["연관1","연관2"]', "auto_json": '["자동1"]'}
        mdb.insert_snapshot(self.conn, k, snap, day="2026-09-21")
        s = mdb.snapshots_for(self.conn, k)[0]
        self.assertEqual(s["related_json"], '["연관1","연관2"]')
        self.assertEqual(s["auto_json"], '["자동1"]')

    def test_migrate_adds_missing_columns(self):
        import sqlite3
        # related_json/auto_json 이 없던 구버전 snapshots 테이블을 흉내낸다.
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.execute("""CREATE TABLE snapshots(
            id INTEGER PRIMARY KEY, keyword_id INTEGER, day TEXT,
            result_count INTEGER, top_json TEXT, ts TEXT, UNIQUE(keyword_id, day))""")
        raw.commit()
        mdb._migrate_snapshots(raw)
        cols = {r["name"] for r in raw.execute("PRAGMA table_info(snapshots)")}
        self.assertIn("related_json", cols)
        self.assertIn("auto_json", cols)
        self.assertIn("review_sum", cols)  # 다른 누락 컬럼도 채워짐
