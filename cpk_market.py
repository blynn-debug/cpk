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
        # 연관검색어·자동완성은 전량 보존한다(대시보드에서 펼치기/접기로 표시).
        "related_json": json.dumps(result.get("related_keywords") or [], ensure_ascii=False),
        "auto_json": json.dumps(result.get("autocomplete") or [], ensure_ascii=False),
        "autocomplete_ok": result.get("autocomplete_ok"),
        "related_ok": result.get("related_ok"),
    }


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
