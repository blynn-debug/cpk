# 마켓 레이더 코어 (저장·소싱·점수·수집) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 쿠팡에서 발굴한 니치 키워드를 매일 수집·저장하고, 경쟁/수요/꾸준함 점수와 도매꾹(국내배송+우수공급사) 존재판별을 붙여 "소싱 가능한 꾸준 니치"를 가려내는 코어 로직을 만든다.

**Architecture:** 맥미니 중심. 순수 로직(저장·점수·소싱·수집 오케스트레이션)은 `cb.search`·네트워크를 주입/모킹해 오프라인 TDD. 실제 크롤은 기존 `cb.search`(프록시·요청제어) 재사용, 스케줄은 launchd 얇은 러너. 저장은 stdlib `sqlite3` (`~/cpk/state/market.db`).

**Tech Stack:** Python 3.12, 표준 라이브러리만(sqlite3, urllib, json, statistics), unittest(레포 관례), 기존 cpk 모듈(`cpk_session`, `cpk_browser`) 재사용. 새 서드파티 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-09-21-market-radar-design.md` (살아있는 문서 — 점수 공식·가중치·규모는 수시 개정). 도매꾹 근거: `side-job-ecommerce/domegg/docs/EXISTENCE_CHECK.md`.

## Global Constraints

- Python 3.12; **새 pip 의존성 금지**(stdlib만). 테스트는 `unittest`, 네트워크 없이(주입/모킹), 임시 경로 사용.
- 상태 경로는 `cpk_session.HOME`(=`~/cpk/state`) 기준. DB: `HOME/market.db`.
- 도매꾹 API Key는 코드에 하드코딩 금지. 순서: 환경변수 `CPK_DOMEGGOOK_KEY` → 파일 `domegg/api_key/api.txt`(경로는 `CPK_DOMEGGOOK_KEY_FILE`로 재정의 가능). 문서·로그에 키 노출 금지.
- 점수는 **순수 함수**로만(부작용 없음), 0.0~1.0 정규화, 서브점수 항상 함께 반환(블랙박스 금지).
- 기존 cpk 용어·패턴을 따른다(새 이름 최소화). 결함마다 회귀 테스트 추가.
- 오프라인 테스트는 `.github/workflows/ci.yml` offline 잡에 포함(push마다 자동 검증).

---

### Task 1: 도매꾹 소싱 클라이언트 (`cpk_domeggook.py`)

**Files:**
- Create: `cpk_domeggook.py`
- Test: `tests/test_domeggook.py`

**Interfaces:**
- Produces:
  - `build_url(keyword: str, key: str, market: str="dome", premium: bool=True, domestic: bool=True, sz: int=5) -> str`
  - `parse_response(body: str) -> dict` → `{"count": int, "samples": list[dict]}`
  - `api_key() -> str`
  - `check_existence(keyword: str, market: str="dome", premium: bool=True, domestic: bool=True, opener=None) -> dict` → `{"keyword","market","exists":bool,"count":int,"samples":list}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_domeggook.py
import json, os, unittest
from unittest import mock
import cpk_domeggook as dg

FIXTURE = json.dumps({"domeggook": {
    "header": {"numberOfItems": 8191},
    "list": {"item": [
        {"title": "니치상품", "price": "3200", "id": "seller1", "nick": "공급사", "url": "http://domeme.com/s/1"}
    ]}}}, ensure_ascii=False)

class BuildUrl(unittest.TestCase):
    def test_both_conditions_and_ver41(self):
        u = dg.build_url("계란보관함", "KEY", market="dome", premium=True, domestic=True, sz=1)
        self.assertIn("https://www.domeggook.com/ssl/api/", u)
        self.assertIn("ver=4.1", u); self.assertIn("mode=getItemList", u)
        self.assertIn("aid=KEY", u); self.assertIn("market=dome", u); self.assertIn("om=json", u)
        self.assertIn("sgd=true", u)      # 우수공급사
        self.assertIn("dfos=false", u)    # 국내배송(해외직배송 제외)
        self.assertIn("sz=1", u)
        self.assertIn("kw=%EA", u)        # 한글 URL 인코딩됨

    def test_no_filters_omits_params(self):
        u = dg.build_url("x", "KEY", premium=False, domestic=False)
        self.assertNotIn("sgd=", u); self.assertNotIn("dfos=", u)

class ParseResponse(unittest.TestCase):
    def test_count_and_samples(self):
        r = dg.parse_response(FIXTURE)
        self.assertEqual(r["count"], 8191)
        self.assertEqual(r["samples"][0]["seller"], "공급사")

    def test_single_item_as_dict(self):
        body = json.dumps({"domeggook": {"header": {"numberOfItems": 1},
                                         "list": {"item": {"title": "t", "id": "s", "url": "u"}}}})
        self.assertEqual(dg.parse_response(body)["count"], 1)
        self.assertEqual(len(dg.parse_response(body)["samples"]), 1)

    def test_zero_results(self):
        body = json.dumps({"domeggook": {"header": {"numberOfItems": 0}, "list": {}}})
        r = dg.parse_response(body)
        self.assertEqual(r["count"], 0); self.assertEqual(r["samples"], [])

class CheckExistence(unittest.TestCase):
    def test_exists_true_via_injected_opener(self):
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return FIXTURE.encode("utf-8")
        with mock.patch.dict(os.environ, {"CPK_DOMEGGOOK_KEY": "KEY"}):
            r = dg.check_existence("계란보관함", opener=lambda url, timeout=0: FakeResp())
        self.assertTrue(r["exists"]); self.assertEqual(r["count"], 8191)
        self.assertEqual(r["market"], "dome")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_domeggook -v`
Expected: FAIL (`No module named cpk_domeggook`).

- [ ] **Step 3: Write minimal implementation**

```python
# cpk_domeggook.py
"""도매꾹 오픈API 존재판별(국내배송+우수공급사). 근거: domegg/docs/EXISTENCE_CHECK.md."""
from __future__ import annotations
import os, re, json, urllib.parse, urllib.request

ENDPOINT = "https://www.domeggook.com/ssl/api/"

def api_key() -> str:
    k = os.environ.get("CPK_DOMEGGOOK_KEY", "").strip()
    if k:
        return k
    path = os.environ.get("CPK_DOMEGGOOK_KEY_FILE",
                          os.path.expanduser("~/side-job-ecommerce/domegg/api_key/api.txt"))
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError as e:
        raise RuntimeError(f"도매꾹 API Key 없음(CPK_DOMEGGOOK_KEY 또는 {path})") from e
    m = re.search(r"[0-9a-fA-F]{20,}", txt)
    return m.group(0) if m else txt.strip()

def build_url(keyword, key, market="dome", premium=True, domestic=True, sz=5) -> str:
    q = {"ver": "4.1", "mode": "getItemList", "aid": key, "market": market,
         "om": "json", "kw": keyword, "sz": str(sz)}
    if premium:
        q["sgd"] = "true"     # 우수공급사(우수판매자)
    if domestic:
        q["dfos"] = "false"   # 국내배송(해외직배송 제외)
    return ENDPOINT + "?" + urllib.parse.urlencode(q)

def parse_response(body: str) -> dict:
    d = json.loads(body)
    root = d.get("domeggook", {})
    count = int(root.get("header", {}).get("numberOfItems", 0) or 0)
    items = root.get("list", {}).get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    samples = [{"title": it.get("title"), "price": it.get("price"),
                "seller": it.get("nick") or it.get("id"), "url": it.get("url")}
               for it in items]
    return {"count": count, "samples": samples}

def check_existence(keyword, market="dome", premium=True, domestic=True, opener=None) -> dict:
    opener = opener or urllib.request.urlopen
    url = build_url(keyword, api_key(), market, premium, domestic, sz=5)
    with opener(url, timeout=20) as r:
        body = r.read().decode("utf-8")
    parsed = parse_response(body)
    return {"keyword": keyword, "market": market, "exists": parsed["count"] > 0,
            "count": parsed["count"], "samples": parsed["samples"]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_domeggook -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add cpk_domeggook.py tests/test_domeggook.py
git commit -m "feat(market): 도매꾹 존재판별 클라이언트(국내배송+우수공급사)"
```

---

### Task 2: 마켓 저장소 스키마·DAO (`cpk_market_db.py`)

**Files:**
- Create: `cpk_market_db.py`
- Test: `tests/test_market_db.py`

**Interfaces:**
- Consumes: 없음(stdlib sqlite3).
- Produces:
  - `connect(path) -> sqlite3.Connection` (스키마 생성, `row_factory=sqlite3.Row`)
  - `upsert_keyword(conn, keyword, source, status="candidate", parent=None) -> int`
  - `set_status(conn, kid, status) -> None`
  - `insert_snapshot(conn, kid, snap: dict, day: str|None=None) -> int`
  - `snapshots_for(conn, kid) -> list[dict]` (day 오름차순)
  - `tracked_keywords(conn) -> list[dict]`
  - `save_sourcing(conn, kid, res: dict) -> None`
  - `save_scores(conn, kid, day, scores: dict) -> None`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_market_db -v`
Expected: FAIL (`No module named cpk_market_db`).

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_market_db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cpk_market_db.py tests/test_market_db.py
git commit -m "feat(market): SQLite 저장소(키워드·스냅샷·소싱·점수)"
```

---

### Task 3: 스냅샷 변환 (`cpk_market.py: snapshot_from_result`)

**Files:**
- Create: `cpk_market.py`
- Test: `tests/test_market_snapshot.py`

**Interfaces:**
- Consumes: `cb.search` 결과 dict 형태(keys: `count`, `total_count`, `badge_counts`, `ads`, `items[]` with `price`,`reviews`,`badge`).
- Produces: `snapshot_from_result(result: dict) -> dict` (Task 2 `SNAP_COLS` 키를 채움).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_snapshot.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_market_snapshot -v`
Expected: FAIL (`No module named cpk_market`).

- [ ] **Step 3: Write minimal implementation**

```python
# cpk_market.py
"""마켓 레이더 오케스트레이션: 스냅샷 변환·발굴 확장."""
from __future__ import annotations
import json, statistics

def snapshot_from_result(result: dict) -> dict:
    items = result.get("items") or []
    bc = result.get("badge_counts") or {}
    prices = [i["price"] for i in items if isinstance(i.get("price"), int)]
    reviews = [int(i.get("reviews") or 0) for i in items]
    rocket = sum(v for k, v in bc.items() if "로켓" in k and "판매자" not in k)
    seller_rocket = sum(v for k, v in bc.items() if "판매자로켓" in k)
    general = sum(v for k, v in bc.items() if "일반" in k)
    top = [{"name": i.get("name"), "price": i.get("price"), "reviews": i.get("reviews"),
            "badge": i.get("badge")} for i in items[:10]]
    return {
        "result_count": result.get("total_count"),
        "units_shown": result.get("count") or len(items),
        "rocket_cnt": rocket, "seller_rocket_cnt": seller_rocket, "general_cnt": general,
        "ad_cnt": result.get("ads") or 0,
        "review_sum": sum(reviews), "review_max": max(reviews) if reviews else 0,
        "price_min": min(prices) if prices else None,
        "price_med": int(statistics.median(prices)) if prices else None,
        "price_max": max(prices) if prices else None,
        "top_json": json.dumps(top, ensure_ascii=False),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_market_snapshot -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cpk_market.py tests/test_market_snapshot.py
git commit -m "feat(market): cb.search 결과 → 스냅샷 변환"
```

---

### Task 4: 점수 (`cpk_market_score.py`)

**Files:**
- Create: `cpk_market_score.py`
- Test: `tests/test_market_score.py`

**Interfaces:**
- Consumes: 스냅샷 dict 리스트(Task 2 `SNAP_COLS`, `day` 포함).
- Produces:
  - `rarity(snap: dict) -> float`
  - `demand(snaps: list[dict], window: int=7) -> float`
  - `steadiness(snaps: list[dict], window: int=7) -> float | None`
  - `opportunity(snaps: list[dict], weights: dict | None=None) -> dict` → `{"rarity","demand","steadiness","opportunity"}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_score.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_market_score -v`
Expected: FAIL (`No module named cpk_market_score`).

- [ ] **Step 3: Write minimal implementation**

```python
# cpk_market_score.py
"""마켓 레이더 점수(순수 함수, 0~1). 공식은 살아있는 문서 — 실데이터로 수시 개정."""
from __future__ import annotations
import math, statistics

RC_CAP = 50000      # 결과수 상한(로그 스케일)
REV_CAP = 100000    # 리뷰합 상한(로그 스케일)
VEL_CAP = 50.0      # 하루 리뷰증가 상한

def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x

def rarity(snap: dict) -> float:
    units = max(snap.get("units_shown") or 0, 1)
    rc = max(snap.get("result_count") or units, 1)
    r_rc = 1 - math.log10(min(rc, RC_CAP)) / math.log10(RC_CAP)
    r_rocket = 1 - (snap.get("rocket_cnt", 0) + snap.get("seller_rocket_cnt", 0)) / units
    r_ad = 1 - (snap.get("ad_cnt", 0) / units)
    return _clamp((max(r_rc, 0) + max(r_rocket, 0) + max(r_ad, 0)) / 3)

def _velocity(snaps, window):
    ws = snaps[-window:] if window else snaps
    if len(ws) < 2:
        return None
    days = (_to_ord(ws[-1]["day"]) - _to_ord(ws[0]["day"])) or 1
    return (ws[-1]["review_sum"] - ws[0]["review_sum"]) / days

def _to_ord(day: str) -> int:
    import datetime
    return datetime.date.fromisoformat(day).toordinal()

def demand(snaps: list[dict], window: int = 7) -> float:
    latest = snaps[-1]
    level = math.log10(max(latest.get("review_sum") or 0, 1) + 1) / math.log10(REV_CAP)
    vel = _velocity(snaps, window)
    if vel is None:
        return _clamp(level)
    vel_score = _clamp(vel / VEL_CAP)
    return _clamp((level + vel_score) / 2)

def steadiness(snaps: list[dict], window: int = 7) -> float | None:
    ws = snaps[-window:] if window else snaps
    if len(ws) < 3:
        return None
    deltas = [ws[i]["review_sum"] - ws[i - 1]["review_sum"] for i in range(1, len(ws))]
    positive_ratio = sum(1 for d in deltas if d > 0) / len(deltas)
    mean = statistics.fmean(deltas)
    if mean <= 0:
        return _clamp(0.1 * positive_ratio)  # 성장 없으면 낮게
    cv = (statistics.pstdev(deltas) / mean) if mean else 1.0
    return _clamp((1 - min(cv, 1.0)) * positive_ratio)

def opportunity(snaps: list[dict], weights: dict | None = None) -> dict:
    w = weights or {"rarity": 1.0, "demand": 1.0, "steadiness": 1.0}
    subs = {"rarity": rarity(snaps[-1]), "demand": demand(snaps), "steadiness": steadiness(snaps)}
    num = sum(w[k] * v for k, v in subs.items() if v is not None)
    den = sum(w[k] for k, v in subs.items() if v is not None) or 1.0
    return {**subs, "opportunity": _clamp(num / den)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_market_score -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cpk_market_score.py tests/test_market_score.py
git commit -m "feat(market): 점수(경쟁·수요·꾸준함·기회, 순수함수)"
```

---

### Task 5: 발굴 확장 (`cpk_market.py: expand_seeds`)

**Files:**
- Modify: `cpk_market.py` (함수 추가)
- Test: `tests/test_market_expand.py`

**Interfaces:**
- Consumes: `related_fn(keyword)->list[str]`, `autocomplete_fn(keyword)->list[str]` (주입 — 운영에선 cpk 연관검색어/자동완성).
- Produces: `expand_seeds(seeds: list[str], related_fn, autocomplete_fn, cap: int=50) -> list[dict]` → `[{"keyword","source","parent"}]` (seed 포함, 중복 제거, cap 상한).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_expand.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_market_expand -v`
Expected: FAIL (`AttributeError: module 'cpk_market' has no attribute 'expand_seeds'`).

- [ ] **Step 3: Write minimal implementation (append to `cpk_market.py`)**

```python
def expand_seeds(seeds, related_fn, autocomplete_fn, cap: int = 50) -> list[dict]:
    out, seen = [], set()

    def add(kw, source, parent):
        kw = (kw or "").strip()
        if kw and kw not in seen and len(out) < cap:
            seen.add(kw)
            out.append({"keyword": kw, "source": source, "parent": parent})

    for seed in seeds:
        add(seed, "seed", None)
    for seed in seeds:
        for fn, src in ((related_fn, "related"), (autocomplete_fn, "auto")):
            try:
                for kw in fn(seed) or []:
                    add(kw, src, seed)
            except Exception:
                continue  # 확장기 실패는 건너뛴다(seed는 이미 포함)
    return out[:cap]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_market_expand -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cpk_market.py tests/test_market_expand.py
git commit -m "feat(market): seed→연관/자동완성 발굴 확장(중복제거·상한)"
```

---

### Task 6: 수집 러너 (`cpk_market_collect.py: run_collection`)

**Files:**
- Create: `cpk_market_collect.py`
- Test: `tests/test_market_collect.py`

**Interfaces:**
- Consumes: `cpk_market_db`(Task2), `cpk_market.snapshot_from_result`(Task3), `cpk_market_score.opportunity`(Task4), `search_fn(keyword)->result dict`(운영=`cb.search`), `sourcing_fn(keyword)->dict`(운영=`cpk_domeggook.check_existence`).
- Produces: `run_collection(conn, keywords, search_fn, sourcing_fn, today: str) -> dict` → `{"collected","failed","stopped"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_collect.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_market_collect -v`
Expected: FAIL (`No module named cpk_market_collect`).

- [ ] **Step 3: Write minimal implementation**

```python
# cpk_market_collect.py
"""마켓 레이더 수집 러너: tracked 키워드마다 검색→스냅샷→점수→소싱 저장.
search_fn/sourcing_fn 을 주입해 오프라인 테스트. 운영에선 cb.search / cpk_domeggook.check_existence."""
from __future__ import annotations
import cpk_market_db as mdb
import cpk_market as m
import cpk_market_score as sc

def run_collection(conn, keywords, search_fn, sourcing_fn, today: str) -> dict:
    collected = failed = 0
    for row in keywords:
        kid, kw = row["id"], row["keyword"]
        try:
            result = search_fn(kw)
        except Exception:
            failed += 1
            continue
        if result.get("outcome") != "ok":
            failed += 1
            continue
        mdb.insert_snapshot(conn, kid, m.snapshot_from_result(result), day=today)
        snaps = mdb.snapshots_for(conn, kid)
        mdb.save_scores(conn, kid, today, sc.opportunity(snaps))
        try:
            mdb.save_sourcing(conn, kid, sourcing_fn(kw))
        except Exception:
            pass  # 소싱 실패는 수집을 막지 않는다(다음 회차 재시도)
        collected += 1
    return {"collected": collected, "failed": failed, "stopped": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_market_collect -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cpk_market_collect.py tests/test_market_collect.py
git commit -m "feat(market): 수집 러너(검색→스냅샷→점수→소싱, 주입식)"
```

---

### Task 7: 운영 진입점 + CI 등록

**Files:**
- Modify: `cpk_market_collect.py` (운영 `main()` 추가 — 실제 `cb.search`·`cpk_domeggook` 연결)
- Modify: `.github/workflows/ci.yml` (새 오프라인 테스트 등록)
- Test: (신규 로직 없음 — main은 배선. 기존 6개 테스트로 커버)

**Interfaces:**
- Consumes: Task 1~6 전부. `cpk_browser.search`, `cpk_domeggook.check_existence`, `cpk_session.HOME`.
- Produces: `python cpk_market_collect.py` 실행 시 tracked 키워드 수집(운영). launchd가 하루 1회 호출(플랜 2 범위지만 진입점은 여기).

- [ ] **Step 1: 운영 main 추가 (append to `cpk_market_collect.py`)**

```python
def main() -> int:
    import cpk_session as cs, cpk_browser as cb, cpk_domeggook as dg, time
    conn = mdb.connect(str(cs.HOME / "market.db"))
    kws = mdb.tracked_keywords(conn)
    if not kws:
        cs.log("market: tracked 키워드 없음"); return 0
    today = time.strftime("%Y-%m-%d")
    r = run_collection(conn, kws,
                       search_fn=lambda kw: cb.search(kw, kind="market"),
                       sourcing_fn=lambda kw: dg.check_existence(kw),
                       today=today)
    cs.log(f"market 수집: {r}")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 2: CI에 새 오프라인 테스트 등록 (`.github/workflows/ci.yml`)**

`Core offline tests` 스텝의 unittest 인자에 새 모듈을 추가한다:

```yaml
      - name: Core offline tests (unit + regression)
        run: >
          python -m unittest
          tests.test_unit tests.test_regression
          tests.test_domeggook tests.test_market_db tests.test_market_snapshot
          tests.test_market_score tests.test_market_expand tests.test_market_collect -v
```

- [ ] **Step 3: 전체 오프라인 스위트 로컬 검증**

Run: `python -m unittest tests.test_domeggook tests.test_market_db tests.test_market_snapshot tests.test_market_score tests.test_market_expand tests.test_market_collect -v`
Expected: PASS (전부).

- [ ] **Step 4: 커밋 + 푸시(자동배포·CI)**

```bash
git add cpk_market_collect.py .github/workflows/ci.yml
git commit -m "feat(market): 운영 진입점(cb.search·도매꾹 연결) + CI에 마켓 테스트 등록"
git push origin main
```

- [ ] **Step 5: CI 초록 확인**

GitHub Actions에서 offline-tests 잡이 새 테스트 포함해 통과하는지 확인.

---

## Self-Review

**1. Spec coverage(코어 부분):**
- 저장 → Task 2(SQLite). 수집 → Task 3(스냅샷 변환)+5(발굴)+6(러너)+7(운영 배선). 점수 → Task 4. 소싱(도매꾹 국내배송+우수공급사) → Task 1 + 6에서 결합. ✅
- 스펙의 대시보드·launchd 스케줄은 **의도적으로 이 플랜 밖**(플랜 2/3). 진입점(main)은 Task 7에 둠.

**2. Placeholder scan:** TBD/TODO 없음. 모든 스텝에 실제 코드·명령·기대결과 명시. ✅

**3. Type consistency:** `snapshot_from_result` 출력 키 = Task 2 `SNAP_COLS`와 일치. `opportunity()`가 반환하는 `{rarity,demand,steadiness,opportunity}` = `save_scores`가 받는 키와 일치. `check_existence` 반환 `{market,exists,count}` = `save_sourcing`가 읽는 키와 일치. `run_collection(conn, keywords, search_fn, sourcing_fn, today)` 시그니처가 Task 6 테스트·Task 7 main 호출과 일치. ✅

**4. 미결(구현 중 결정):** `so`(정렬)·가격 통화 단위 등은 기본값 사용. 점수 상한(RC_CAP/REV_CAP/VEL_CAP)은 초기값이며 살아있는 문서로 실데이터 후 튜닝.

---

## 후속 플랜 (이 플랜 밖)

- **플랜 2 (스케줄·발굴 운영)**: launchd 하루 1회 러너, seed 관리(`state/seeds.txt`), 후보→tracked 승격 CLI.
- **플랜 3 (대시보드)**: `cpk_market_report.py`(맥미니) + Railway `/markets` 페이지(읽기 전용, 추이 그래프·소싱뱃지).
