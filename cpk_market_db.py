# cpk_market_db.py
"""마켓 레이더 SQLite 저장소. 키워드·스냅샷·소싱·점수."""
from __future__ import annotations
import sqlite3, time

SNAP_COLS = ["result_count", "units_shown", "rocket_cnt", "seller_rocket_cnt", "general_cnt",
             "ad_cnt", "review_sum", "review_max", "price_min", "price_med", "price_max", "top_json"]

def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS keywords(
        id INTEGER PRIMARY KEY, keyword TEXT UNIQUE, source TEXT,
        status TEXT DEFAULT 'candidate', parent TEXT, added_at TEXT)""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS snapshots(
        id INTEGER PRIMARY KEY, keyword_id INTEGER, day TEXT,
        {", ".join(c + " INTEGER" for c in SNAP_COLS if c != "top_json")}, top_json TEXT,
        ts TEXT, UNIQUE(keyword_id, day))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sourcing(
        keyword_id INTEGER PRIMARY KEY, market TEXT, dome_exists INTEGER, dome_count INTEGER,
        checked_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS scores(
        keyword_id INTEGER PRIMARY KEY, day TEXT,
        rarity REAL, demand REAL, steadiness REAL, opportunity REAL)""")
    conn.commit()
    return conn

def upsert_keyword(conn, keyword, source, status="candidate", parent=None) -> int:
    cur = conn.execute("SELECT id FROM keywords WHERE keyword=?", (keyword,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO keywords(keyword,source,status,parent,added_at) VALUES(?,?,?,?,?)",
                       (keyword, source, status, parent, time.strftime("%Y-%m-%dT%H:%M:%S")))
    conn.commit()
    return cur.lastrowid

def set_status(conn, kid, status) -> None:
    conn.execute("UPDATE keywords SET status=? WHERE id=?", (status, kid))
    conn.commit()

def insert_snapshot(conn, kid, snap: dict, day: str | None = None) -> int:
    day = day or time.strftime("%Y-%m-%d")
    cols = ["keyword_id", "day"] + SNAP_COLS + ["ts"]
    vals = [kid, day] + [snap.get(c) for c in SNAP_COLS] + [time.strftime("%Y-%m-%dT%H:%M:%S")]
    ph = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO snapshots({','.join(cols)}) VALUES({ph}) "
                 f"ON CONFLICT(keyword_id,day) DO UPDATE SET "
                 f"{', '.join(c+'=excluded.'+c for c in SNAP_COLS)}, ts=excluded.ts", vals)
    conn.commit()
    return conn.execute("SELECT id FROM snapshots WHERE keyword_id=? AND day=?", (kid, day)).fetchone()["id"]

def snapshots_for(conn, kid) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM snapshots WHERE keyword_id=? ORDER BY day ASC", (kid,))]

def tracked_keywords(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT k.id, k.keyword, k.source, k.status,
               s.dome_exists, s.dome_count, sc.opportunity, sc.rarity, sc.demand, sc.steadiness
        FROM keywords k
        LEFT JOIN sourcing s ON s.keyword_id=k.id
        LEFT JOIN scores sc ON sc.keyword_id=k.id
        WHERE k.status='tracked' ORDER BY k.keyword ASC""")]

def save_sourcing(conn, kid, res: dict) -> None:
    conn.execute("""INSERT INTO sourcing(keyword_id,market,dome_exists,dome_count,checked_at)
        VALUES(?,?,?,?,?) ON CONFLICT(keyword_id) DO UPDATE SET
        market=excluded.market, dome_exists=excluded.dome_exists,
        dome_count=excluded.dome_count, checked_at=excluded.checked_at""",
        (kid, res.get("market"), 1 if res.get("exists") else 0, res.get("count"),
         time.strftime("%Y-%m-%dT%H:%M:%S")))
    conn.commit()

def save_scores(conn, kid, day, scores: dict) -> None:
    conn.execute("""INSERT INTO scores(keyword_id,day,rarity,demand,steadiness,opportunity)
        VALUES(?,?,?,?,?,?) ON CONFLICT(keyword_id) DO UPDATE SET
        day=excluded.day, rarity=excluded.rarity, demand=excluded.demand,
        steadiness=excluded.steadiness, opportunity=excluded.opportunity""",
        (kid, day, scores.get("rarity"), scores.get("demand"),
         scores.get("steadiness"), scores.get("opportunity")))
    conn.commit()
