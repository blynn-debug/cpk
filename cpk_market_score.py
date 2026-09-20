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
