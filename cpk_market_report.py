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
