"""주기 실행용 헬스체크: 실제 Chrome으로 쿠팡 검색이 되는지 확인하고 health.json에 기록한다.

검색·중단·예산은 공용 관문(cs.request_gate)이 처리하고, 명확한 차단 시 공통 중단은 검색 경로가
설정한다(cs.maybe_pause_on_block). 이 모듈은 상태 기록과 알림만 담당한다.

환경변수:
  CPK_HOME        상태 폴더 (기본 ~/cpk/state)
  CPK_NTFY_TOPIC  ntfy 토픽 (없으면 알림 생략)
"""
from __future__ import annotations

import os
import sys
import urllib.request

import cpk_session as cs

FAIL_ALERT_AT = 2


def notify(title: str, body: str) -> None:
    topic = os.environ.get("CPK_NTFY_TOPIC")
    if not topic:
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                                     headers={"Title": title.encode("utf-8").decode("latin-1", "ignore")})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:  # 알림 실패는 치명적이지 않음
        cs.log(f"ntfy 실패: {e}")


def main() -> int:
    """실제 Chrome으로 검색 canary. 파이썬 쿠키 재발급 경로(구형)는 폐기했다."""
    import cpk_browser as cb
    if not cb.available():
        cs.log("keepalive: Chrome 없음")
        return 1
    res = cb.search("계란보관함", kind="health")
    outcome = res["outcome"]
    if outcome in ("busy", "paused", "budget"):
        cs.log(f"keepalive: {outcome} — 건너뜀")
        return 0
    # 정상 결과(ok)와 정상 무결과(no_results)는 세션이 살아 있는 것으로 본다. 차단·오류만 실패로 센다.
    ok = outcome in ("ok", "no_results")

    # 카운터 유실 방지: health 읽기→증가→저장을 잠금 구간에서 원자적으로 한다.
    def _mutate(h):
        h["runs"] = int(h.get("runs", 0)) + 1
        h["fails"] = 0 if ok else int(h.get("fails", 0)) + 1
        h["ok"] = ok
        h["mode"] = "browser"
        h["outcome"] = outcome
        h["http_status"] = res["http_status"]
        h["units"] = res["count"]
        return h

    h = cs.update_health(_mutate)
    cs.log(f"keepalive(browser): outcome={outcome} http={res['http_status']} count={res['count']} fails={h['fails']}")
    # 공통 중단은 검색 경로(cs.maybe_pause_on_block)가 이미 처리한다 — 여기서 중복으로 걸지 않는다.
    if not ok and h["fails"] == FAIL_ALERT_AT:
        notify("cpk 검색 실패", f"쿠팡 검색이 되지 않습니다(outcome={outcome}). 확인이 필요합니다.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
