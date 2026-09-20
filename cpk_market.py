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
