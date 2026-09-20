# 마켓 레이더 플랜 2 — 스케줄·seed·승격

> For agentic workers: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** seed 키워드를 확장·저장하고 후보→tracked 승격하는 관리 모듈 + 매일 자동 수집(launchd).
**Architecture:** 맥미니. seed 관리는 오프라인 TDD(확장기 주입), 스케줄은 launchd가 `cpk_market_collect.py` 호출.
**Tech Stack:** Python 3.12 stdlib, unittest, 기존 cpk_market_db/cpk_market 재사용.
**Spec:** docs/superpowers/specs/2026-09-21-market-radar-design.md (살아있는 문서).

## Global Constraints
- stdlib만. 로컬 인터프리터 `/c/Users/djliz/AppData/Local/Programs/Python/Python312-arm64/python.exe`. 테스트 repo root에서 `-m unittest`.
- 기존 모듈(cpk_market_db, cpk_market) 수정 금지 — 새 파일만.
- 브랜치 작업, push 금지(병합은 컨트롤러).

---

### Task 1: seed 관리 모듈 (`cpk_market_seeds.py`)

**Files:** Create `cpk_market_seeds.py`, Test `tests/test_market_seeds.py`

**Interfaces:**
- Produces:
  - `expand_and_store(conn, seeds, related_fn, autocomplete_fn, cap=50) -> int` (후보로 저장, 추가된 신규 개수 반환)
  - `promote(conn, keyword) -> bool` / `archive(conn, keyword) -> bool`
  - `candidates(conn) -> list[dict]`
- Consumes: `cpk_market.expand_seeds`, `cpk_market_db`(connect/upsert_keyword/set_status).

- [ ] **Step 1: Write failing test**

```python
# tests/test_market_seeds.py
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
```

- [ ] **Step 2: Run to verify fail** — `... -m unittest tests.test_market_seeds -v` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
# cpk_market_seeds.py
"""마켓 레이더 seed 관리: 확장·저장·승격. CLI 포함(운영은 cb 연관검색어/자동완성 주입)."""
from __future__ import annotations
import cpk_market_db as mdb
import cpk_market as m

def expand_and_store(conn, seeds, related_fn, autocomplete_fn, cap: int = 50) -> int:
    added = 0
    for item in m.expand_seeds(seeds, related_fn, autocomplete_fn, cap=cap):
        before = conn.execute("SELECT id FROM keywords WHERE keyword=?", (item["keyword"],)).fetchone()
        mdb.upsert_keyword(conn, item["keyword"], item["source"], status="candidate", parent=item.get("parent"))
        if before is None:
            added += 1
    return added

def _id_of(conn, keyword):
    row = conn.execute("SELECT id FROM keywords WHERE keyword=?", (keyword,)).fetchone()
    return row["id"] if row else None

def promote(conn, keyword) -> bool:
    kid = _id_of(conn, keyword)
    if kid is None:
        return False
    mdb.set_status(conn, kid, "tracked")
    return True

def archive(conn, keyword) -> bool:
    kid = _id_of(conn, keyword)
    if kid is None:
        return False
    mdb.set_status(conn, kid, "archived")
    return True

def candidates(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, keyword, source, parent FROM keywords WHERE status='candidate' ORDER BY keyword")]

def main() -> int:
    import argparse, sys, cpk_session as cs
    ap = argparse.ArgumentParser(prog="cpk_market_seeds")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("seedfile")
    p = sub.add_parser("promote"); p.add_argument("keywords", nargs="+")
    sub.add_parser("list")
    args = ap.parse_args()
    conn = mdb.connect(str(cs.HOME / "market.db"))
    if args.cmd == "add":
        import cpk_browser as cb
        seeds = [ln.strip() for ln in open(args.seedfile, encoding="utf-8")
                 if ln.strip() and not ln.startswith("#")]
        def related_fn(kw):
            r = cb.search(kw, kind="market")
            return r.get("related_keywords", []) if r.get("outcome") == "ok" else []
        def auto_fn(kw):
            return cs.autocomplete_keywords(kw).get("items", [])
        n = expand_and_store(conn, seeds, related_fn, auto_fn)
        cs.log(f"market seeds: +{n} 후보 (seeds {len(seeds)})")
    elif args.cmd == "promote":
        for kw in args.keywords:
            print(kw, "→", "tracked" if promote(conn, kw) else "없음")
    elif args.cmd == "list":
        for c in candidates(conn):
            print("[후보]", c["keyword"], f"({c['source']})")
        for t in mdb.tracked_keywords(conn):
            print("[추적]", t["keyword"], "opp=", t.get("opportunity"))
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass** — `... -m unittest tests.test_market_seeds -v` → PASS (4).
- [ ] **Step 5: Commit** — `git add cpk_market_seeds.py tests/test_market_seeds.py && git commit -m "feat(market): seed 관리(확장·저장·승격) + CLI"`

---

### Task 2: launchd 스케줄 + 예시 seed + 실수집 검증 (인프라 — 컨트롤러 수행)

**Files:** Create `deploy/com.cpk.market.plist`, `state/market_seeds.example.txt`; Modify `deploy/install_mac.sh`(잡 등록, 선택).

- launchd plist: 매일 04:20 `~/cpk/.venv/bin/python ~/cpk/cpk_market_collect.py` 실행(로그 state/market.out.log). `RunAtLoad=false`.
- 예시 seed 파일(사용자 교체 전제) 소량.
- 컨트롤러가 맥미니에 배포 후: 예시 seed로 add→promote 3~5개→`cpk_market_collect.py` 1회 실제 실행→market.db 채워짐 확인(스냅샷·점수·소싱). launchd 등록·검증.
