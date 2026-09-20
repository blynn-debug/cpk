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
