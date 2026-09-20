# 마켓 레이더 플랜 3 — 대시보드 (Railway /markets)

> For agentic workers: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** 맥미니 market.db의 기회점수·추이·도매꾹 소싱을 Railway 웹 `/markets`에서 읽기전용으로 본다.
**Architecture:** 맥미니 `cpk_market_report.py`가 JSON 리포트를 만들고, 기존 제한키 SSH(:443) forced-command가 검색/리포트를 분기. Railway가 그 JSON을 받아 대시보드 렌더.
**Tech Stack:** Python 3.12 stdlib(리포트), Flask(웹, 기존 web/), unittest.
**Spec:** docs/superpowers/specs/2026-09-21-market-radar-design.md (살아있는 문서). 소싱은 dome 전용(플랜1 스키마; supply는 향후).

## Global Constraints
- stdlib만(리포트). 로컬 인터프리터 `/c/Users/djliz/AppData/Local/Programs/Python/Python312-arm64/python.exe`. 웹 테스트는 `web/`에서 unittest, ssh 목.
- 리포트 모듈은 import 시 curl_cffi 안 딸려와야(main 지연 임포트).
- 브랜치 작업, push 금지(병합은 컨트롤러).

---

### Task 1: 리포트 모듈 (`cpk_market_report.py`)

**Files:** Create `cpk_market_report.py`, Test `tests/test_market_report.py`

**Interfaces:**
- Produces: `report(conn) -> dict` → `{"generated": str, "markets": [ {keyword, opportunity, rarity, demand, steadiness, dome_exists, dome_count, latest:{day,review_sum,price_med,units_shown,result_count}, trend:[{day,review_sum,price_med,rocket_ratio}]} ] }` (기회점수 내림차순, None은 뒤로).
- Consumes: `cpk_market_db`(connect/tracked_keywords/snapshots_for).

- [ ] **Step 1: Write failing test**

```python
# tests/test_market_report.py
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
```

- [ ] **Step 2: Run to verify fail** — `... -m unittest tests.test_market_report -v` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
# cpk_market_report.py
"""마켓 레이더 리포트: market.db → 대시보드용 JSON. import 시 크롤러 의존성 없음."""
from __future__ import annotations
import json, time
import cpk_market_db as mdb

def _trend_point(s: dict) -> dict:
    units = s.get("units_shown") or 0
    ratio = ((s.get("rocket_cnt", 0) + s.get("seller_rocket_cnt", 0)) / units) if units else 0.0
    return {"day": s["day"], "review_sum": s.get("review_sum"),
            "price_med": s.get("price_med"), "rocket_ratio": round(ratio, 4)}

def report(conn) -> dict:
    markets = []
    for row in mdb.tracked_keywords(conn):
        snaps = mdb.snapshots_for(conn, row["id"])
        latest = snaps[-1] if snaps else {}
        markets.append({
            "keyword": row["keyword"],
            "opportunity": row.get("opportunity"),
            "rarity": row.get("rarity"), "demand": row.get("demand"),
            "steadiness": row.get("steadiness"),
            "dome_exists": row.get("dome_exists"), "dome_count": row.get("dome_count"),
            "latest": {"day": latest.get("day"), "review_sum": latest.get("review_sum"),
                       "price_med": latest.get("price_med"), "units_shown": latest.get("units_shown"),
                       "result_count": latest.get("result_count")},
            "trend": [_trend_point(s) for s in snaps],
        })
    markets.sort(key=lambda m: (m["opportunity"] is None, -(m["opportunity"] or 0)))
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "markets": markets}

def main() -> int:
    import cpk_session as cs
    conn = mdb.connect(str(cs.HOME / "market.db"))
    print(json.dumps(report(conn), ensure_ascii=False))
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass** — PASS (3).
- [ ] **Step 5: Commit** — `git add cpk_market_report.py tests/test_market_report.py && git commit -m "feat(market): 리포트 모듈(market.db→대시보드 JSON)"`

---

### Task 2: 웹 대시보드 (`web/`) + forced-command 분기

**Files:** Modify `web/app.py`(라우트 추가), `web/miniclient.py`(market_report 함수), Create `web/templates/markets.html`, Modify `deploy/cpk_ssh_search.sh`(리포트 분기); Test `web/tests/test_web.py`(추가).

**Interfaces:**
- `miniclient.market_report() -> dict`: SSH로 원격 명령 `__market_report__` 전송(기존 build_ssh_command 재사용, keyword 자리에 sentinel), 맥미니 forced-command가 이를 보고 `cpk_market_report.py` 실행. 반환 JSON 파싱.
- `deploy/cpk_ssh_search.sh`: `SSH_ORIGINAL_COMMAND`가 `__market_report__`면 `cpk_market_report.py`, 아니면 `cpk_search_json.py`.

- [ ] **Step 1: Write failing test (web)**

```python
# web/tests/test_web.py 에 추가
class MarketReport(unittest.TestCase):
    def setUp(self):
        import importlib, os
        os.environ.pop("APP_PASSWORD", None)
        import app as appmod; importlib.reload(appmod)
        self.app = appmod; self.client = appmod.app.test_client()

    def test_api_markets_calls_miniclient(self):
        from unittest import mock
        fake = {"generated": "t", "markets": [{"keyword": "계란보관함", "opportunity": 0.6,
                 "dome_exists": 1, "dome_count": 42, "latest": {"review_sum": 1200},
                 "rarity": .7, "demand": .6, "steadiness": .5, "trend": []}]}
        with mock.patch.object(self.app.miniclient, "market_report", return_value=fake) as m:
            r = self.client.get("/api/markets")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["markets"][0]["keyword"], "계란보관함")
        m.assert_called_once()

    def test_markets_page_renders(self):
        r = self.client.get("/markets")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"markets", r.data.lower())
```

- [ ] **Step 2: Run to verify fail** — `cd web && ... -m unittest tests.test_web -v` → FAIL (no /api/markets, no market_report).

- [ ] **Step 3: Implement**

`web/miniclient.py` — add:
```python
MARKET_SENTINEL = "__market_report__"

def market_report() -> dict:
    """맥미니 리포트를 SSH로 가져온다. 실패는 {'markets':[], 'error':..} 로."""
    try:
        key_path = _key_path()
    except Exception as e:
        return {"markets": [], "error": str(e), "message": human_message("error")}
    cmd = build_ssh_command(MARKET_SENTINEL, key_path)
    timeout = float(os.environ.get("SSH_TIMEOUT", "170"))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"markets": [], "error": "timeout", "message": human_message("timeout")}
    if p.returncode != 0 and not p.stdout.strip():
        return {"markets": [], "error": (p.stderr or "").strip()[-300:],
                "message": human_message("transport")}
    res = parse_output(p.stdout)
    res.setdefault("markets", [])
    return res
```
(Note: `build_ssh_command`'s keyword validity check is in `search()`, not `build_ssh_command`; the sentinel is passed only through build_ssh_command, so it is not rejected. `valid_keyword` is NOT applied to the sentinel.)

`web/app.py` — add:
```python
@app.get("/markets")
def markets_page():
    return render_template("markets.html")

@app.get("/api/markets")
def api_markets():
    return jsonify(miniclient.market_report())
```

`deploy/cpk_ssh_search.sh` — change the exec line to route:
```sh
if [ "${SSH_ORIGINAL_COMMAND:-}" = "__market_report__" ]; then
  exec .venv/bin/python cpk_market_report.py
fi
exec .venv/bin/python cpk_search_json.py
```
(keep the env-load + web overrides above it.)

`web/templates/markets.html` — read-only dashboard: fetch `/api/markets`, render a table (keyword, opportunity, rarity/demand/steadiness, dome badge + count, latest review_sum/price_med) sorted server-side; each row a small trend sparkline from `trend[].review_sum`. Include a heading containing the word "markets". (Author a clean table page; reuse the visual style of index.html — system font, light/dark tokens, side gutter, responsive.)

- [ ] **Step 4: Run to verify pass** — web unittest PASS (existing + 2 new).
- [ ] **Step 5: Commit** — `git add web/ deploy/cpk_ssh_search.sh && git commit -m "feat(market): Railway /markets 대시보드 + forced-command 리포트 분기"`

---

### Task 3: 배포·검증 (인프라 — 컨트롤러)
- push_to_mini.sh 에 cpk_market_report.py 추가 → 맥미니 배포. cpk_ssh_search.sh 갱신 배포.
- main 병합 → Railway 자동배포(web 변경 있으니 재배포됨).
- 검증: 전용 키로 `__market_report__` 호출 → 맥미니 리포트 JSON 수신 확인. Railway `/api/markets`가 200+markets 반환, `/markets` 페이지 렌더 확인.
