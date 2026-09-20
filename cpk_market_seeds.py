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
