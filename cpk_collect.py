"""사용자가 요청한 검색어 목록을 순차 수집해 파일로 쌓는다.

예약 실행하지 않는다. 한 번 실행하면 keywords.txt의 검색어를 차례로 검색하고
결과를 data/ 아래에 저장한 뒤 종료한다. 중단/예산으로 끝난 작업의 자동 재개는 아직 없다.

안정 운영 규칙:
  - 검색 사이 간격을 무작위(기본 25~60초)로 둔다.
  - 하루 공통 요청 상한(기본 300)은 자동완성·워밍업·재시도도 포함한다.
  - 브라우저 검색의 명확한 차단은 공통 중단으로 전파한다(기본 6시간).
  - 시간대 제한은 기본 없음. 사용자가 설정한 CPK_QUIET_HOURS는 적용한다.
  - 동시 수집을 막고, 모든 검색은 공통 요청 간격·예산을 따른다.

파일:
  ~/cpk/keywords.txt              한 줄에 검색어 하나. '#' 주석. '검색어 | pages=2' 로 페이지 수 지정.
  ~/cpk/data/YYYY-MM-DD/<검색어>.json   그날 마지막 수집 결과(전체)
  ~/cpk/data/latest/<검색어>.json       가장 최근 결과
  ~/cpk/data/runs.jsonl               수집 1건당 요약 한 줄(시각·상태·건수·배지·광고·상위 10개)
  ~/cpk/state/collect.json            일일 카운터·backoff·마지막 실행

환경변수(cpk.env):
  CPK_COLLECT_GAP_MIN / CPK_COLLECT_GAP_MAX   검색 간격 초 (기본 25 / 60)
  CPK_DAILY_BUDGET                             하루 공통 요청 상한 (기본 300)
  CPK_BACKOFF_HOURS                            429/403 후 대기 시간 (기본 6)
  CPK_QUIET_HOURS                              조용한 시간대 "02-06" (빈 값이면 없음)
  CPK_SYNC_TARGET                              결과를 rsync로 보낼 ssh 대상 (예: aws103:cpk-data/)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import cpk_session as cs
import cpk_search as sr

APP = Path(os.environ.get("CPK_APP", Path(__file__).resolve().parent))
KEYWORDS = APP / "keywords.txt"
DATA = Path(os.environ.get("CPK_DATA", APP / "data"))
STATE = cs.HOME / "collect.json"

GAP_MIN = float(os.environ.get("CPK_COLLECT_GAP_MIN", "25"))
GAP_MAX = float(os.environ.get("CPK_COLLECT_GAP_MAX", "60"))
DAILY_BUDGET = int(os.environ.get("CPK_DAILY_BUDGET", "300"))
BACKOFF_HOURS = float(os.environ.get("CPK_BACKOFF_HOURS", "6"))
QUIET = os.environ.get("CPK_QUIET_HOURS", "").strip()
SYNC_TARGET = os.environ.get("CPK_SYNC_TARGET", "").strip()
# browser: 실제 Chrome(CDP)으로 검색 — 아카마이 봇차단을 우회(권장/기본). python: curl_cffi 직접 요청.
MODE = os.environ.get("CPK_COLLECT_MODE", "browser").strip()


# ---------- 상태 ----------

def read_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def write_state(st: dict) -> None:
    st["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    cs.atomic_write(STATE, json.dumps(st, ensure_ascii=False, indent=1))


def today() -> str:
    return dt.date.today().isoformat()


def in_quiet_hours(now: dt.datetime | None = None) -> bool:
    if not QUIET:
        return False
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", QUIET)
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    h = (now or dt.datetime.now()).hour
    return a <= h < b if a <= b else (h >= a or h < b)


# ---------- 검색어 ----------

def load_keywords() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if not KEYWORDS.exists():
        return out
    for line in KEYWORDS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pages = 1
        if "|" in line:
            kw, opts = [x.strip() for x in line.split("|", 1)]
            m = re.search(r"pages\s*=\s*(\d+)", opts)
            if m:
                pages = max(1, min(int(m.group(1)), 5))
        else:
            kw = line
        out.append((kw, pages))
    return out


def safe_name(q: str) -> str:
    return re.sub(r"[^\w가-힣]+", "_", q).strip("_")[:60] or "q"


# ---------- 저장 ----------

def save_result(res: dict) -> None:
    q = res["query"]
    day_dir = DATA / today()
    latest = DATA / "latest"
    day_dir.mkdir(parents=True, exist_ok=True)
    latest.mkdir(parents=True, exist_ok=True)
    res["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    body = json.dumps(res, ensure_ascii=False, indent=1)
    name = safe_name(q) + (f"_p{res['page']}" if res.get("page", 1) > 1 else "") + ".json"
    cs.atomic_write(day_dir / name, body)   # 부분 파일 방지
    cs.atomic_write(latest / name, body)
    items = res.get("items", [])
    reviews = [it.get("reviews", 0) or 0 for it in items]
    summary = {
        "ts": res["collected_at"], "run_id": res.get("run_id"),
        "query": q, "page": res.get("page", 1),
        "outcome": res.get("outcome", "ok"), "http_status": res.get("http_status"),
        "count": res["count"], "total_count": res.get("total_count"),
        "badge_counts": res.get("badge_counts"), "ads": res.get("ads"),
        "related": res.get("related_keywords", [])[:10],
        "autocomplete": res.get("autocomplete", [])[:10],
        "review_total": sum(reviews), "review_max": max(reviews) if reviews else 0,
        "top": [{"rank": i + 1, "id": it.get("product_id"), "price": it.get("price"),
                 "badge": it.get("badge"), "ad": it.get("ad"), "reviews": it.get("reviews")}
                for i, it in enumerate(items[:10])],
    }
    _append_run(summary)


def _append_run(summary: dict) -> None:
    cs.append_line(DATA / "runs.jsonl", json.dumps(summary, ensure_ascii=False))


def _record_failure(query: str, page: int, r: dict, run_id=None) -> None:
    """실패한 조회도 runs.jsonl에 남겨 성공률·신선도 계산의 분모가 되게 한다(결과 파일은 안 만듦)."""
    _append_run({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run_id": run_id,
                 "query": query, "page": page, "outcome": r.get("outcome"),
                 "http_status": r.get("http_status"), "error": r.get("error"), "count": 0})


def sync() -> None:
    if not SYNC_TARGET:
        return
    try:
        r = subprocess.run(["rsync", "-az", "--timeout=60", str(DATA) + "/", SYNC_TARGET],
                           capture_output=True, text=True, timeout=300)
        cs.log(f"sync -> {SYNC_TARGET} rc={r.returncode}" + (f" err={r.stderr.strip()[:120]}" if r.returncode else ""))
    except Exception as e:
        cs.log(f"sync 실패: {e!r}")


# ---------- 본체 ----------

def run(dry: bool = False, limit: int | None = None) -> int:
    st = read_state()
    now = time.time()

    if st.get("day") != today():
        st.update({"day": today(), "fetched_today": 0, "runs_today": 0})

    # 파이썬 경로만 쿠키통이 필요하다. 브라우저 경로는 Chrome 프로필이 쿠키를 관리한다.
    if MODE == "python" and not cs.JAR.exists():
        cs.log("collect: jar.json 없음 — 건너뜀")
        return 2
    # 파이썬 경로일 때만 세션 상태로 건너뛴다. 브라우저 경로는 파이썬 세션 상태와 무관하게 동작한다.
    if MODE == "python":
        h = cs.read_health()
        if h and not h.get("ok", True) and int(h.get("fails", 0)) >= 2:
            cs.log(f"collect: 세션 실패 상태(fails={h.get('fails')}) — 건너뜀")
            return 3
    rem = cs.paused_remaining()
    if rem > 0:
        cs.log(f"collect: 공통 중단 상태({rem / 3600:.1f}h 남음) — 건너뜀")
        return 4
    if in_quiet_hours():
        cs.log(f"collect: 조용한 시간대({QUIET}) — 건너뜀")
        return 5

    kws = load_keywords()
    if not kws:
        cs.log(f"collect: 검색어 없음 ({KEYWORDS})")
        return 6
    if limit:
        kws = kws[:limit]

    # 예산은 공통 요청 관문(cs.request_gate)이 모든 경로 합산으로 강제한다. 여기선 사전 소진만 확인.
    if cs.today_count() >= cs.DAILY_BUDGET:
        cs.log(f"collect: 공통 일일 예산({cs.DAILY_BUDGET}) 도달 — 건너뜀")
        return 7

    if dry:
        # dry는 순서만 확인한다. Chrome·상태·요청을 만들지 않고 여기서 끝낸다.
        for kw, pages in kws:
            for page in range(1, pages + 1):
                cs.log(f"collect(dry): {kw} p{page}")
        return 0

    # 동시 수집 방지: 이미 수집 실행 중이면(수동+타이머 겹침) 시작하지 않는다(카운터 유실·부분 파일 방지).
    lock = cs._flock("collect.run.lock", blocking=False)
    if not lock.__enter__():
        cs.log("collect: 이미 수집 실행 중 — 건너뜀")
        return 9
    try:
        import uuid
        # 실행 잠금을 얻은 뒤 최신 상태를 다시 읽는다 — 잠금 전 읽은 값으로 카운터를 덮어쓰지 않게.
        st = read_state()
        if st.get("day") != today():
            st.update({"day": today(), "fetched_today": 0, "runs_today": 0})
        st["run_id"] = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        st["runs_today"] = int(st.get("runs_today", 0)) + 1
        st["last_start"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_state(st)

        if MODE == "browser":
            import cpk_browser as cb
            if not cb.available():
                cs.log("collect: Chrome 없음 — python 경로로 대체")
                ok_n, fail_n, empty_n, stopped = _run_python(st, kws, dry)
            else:
                with cb.search_session("collect") as bfetch:
                    if bfetch is None:
                        cs.log("collect: 브라우저 세션을 얻지 못함(다른 작업 중) — 건너뜀")
                        return 8
                    ok_n, fail_n, empty_n, stopped = _run_loop(
                        st, kws, dry, fetch=lambda kw, page: bfetch(kw, page),
                        autocomplete=_browser_autocomplete, allow_reissue=False)
        else:
            ok_n, fail_n, empty_n, stopped = _run_python(st, kws, dry)

        st["last_result"] = {"run_id": st.get("run_id"), "ok": ok_n, "fail": fail_n,
                             "empty": empty_n, "stopped": stopped, "mode": MODE,
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        write_state(st)
        cs.log(f"collect 종료({MODE}, {st['run_id']}): ok={ok_n} empty={empty_n} fail={fail_n} "
               f"stopped={stopped}")
        if not dry:
            sync()
    finally:
        lock.__exit__(None, None, None)
    # 종료 코드: 차단(blocked:*)=1, 실패 있음=2, 정상(성공/무결과/예산·중단으로 정상 정지)=0
    if stopped and stopped not in ("budget", "paused"):
        return 1
    if fail_n > 0:
        return 2
    return 0


def _browser_autocomplete(kw: str) -> list:
    """수집 경로의 자동완성. 공용 헬퍼(cs.autocomplete_keywords)를 써서 검색·키워드 CLI와 경로를 통일한다."""
    return cs.autocomplete_keywords(kw)["items"]


def _run_python(st, kws, dry):
    """파이썬(curl_cffi) 경로. 차단 시 브라우저로 1회 재발급 후 같은 검색어 재시도."""
    auto_reissue = os.environ.get("CPK_AUTO_REISSUE", "1") != "0"
    ctx = {"reissued": False, "s": None}
    with cs.jar_lock():
        ctx["s"] = cs.make_session()

        def fetch(kw, page):
            import cpk_browser as cb
            try:
                with cs.request_gate("collect"):
                    status, html = cs.search_html(ctx["s"], kw, page)
            except cs.RequestPaused as e:
                return {"outcome": "paused", "http_status": None, "product_count": 0,
                        "html": "", "final_url": None, "title": None, "error": None,
                        "remaining": e.remaining, "reason": e.reason}
            except cs.BudgetExceeded:
                return {"outcome": "budget", "http_status": None, "product_count": 0,
                        "html": "", "final_url": None, "title": None, "error": None}
            units = cs.units_in(html)
            return {"http_status": status, "outcome": cb.classify_outcome(status, html, units),
                    "product_count": units, "html": html, "final_url": None, "title": None, "error": None}

        def ac(kw):
            return cs.autocomplete_keywords(kw, ctx["s"])["items"]

        def on_block(kw, page, outcome, attempt):
            # 첫 차단 때 한 번만 브라우저 재발급 → 파이썬 세션 새로 만들고 재시도 신호
            if auto_reissue and not ctx["reissued"] and attempt == 1:
                cs.log(f"collect: {kw} p{page} {outcome} — 브라우저 재발급 후 재시도")
                try:
                    import cpk_browser as cb
                    ok_re = cb.available() and cb.reissue(verify=True, hold_lock=True)
                except Exception as e:
                    cs.log(f"collect: 재발급 오류 {e!r}")
                    ok_re = False
                ctx["reissued"] = True
                if ok_re:
                    st["reissued_after_block"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    ctx["s"] = cs.make_session()
                    return True  # 재시도
            return False

        return _run_loop(st, kws, dry, fetch=fetch,
                         autocomplete=ac, allow_reissue=True, on_block=on_block)


def _run_loop(st, kws, dry, fetch, autocomplete, allow_reissue, on_block=None):
    """검색어 순회 공통 루프. fetch(kw,page)->dict(outcome·html·http_status…), autocomplete(kw)->list.
    outcome으로 정상·무결과·차단·오류·중단·예산을 구분한다. 예산·간격·중단은 fetch 내부의 공통
    관문(cs.request_gate)이 처리한다. 반환: (성공, 실패, 무결과, stopped)."""
    ok_n = fail_n = empty_n = 0
    stopped = None
    first = True
    for kw, pages in kws:
        for page in range(1, pages + 1):
            if not first:
                time.sleep(random.uniform(GAP_MIN, GAP_MAX))
            first = False
            if dry:
                cs.log(f"collect(dry): {kw} p{page}")
                continue
            attempt = 0
            while True:
                attempt += 1
                try:
                    r = fetch(kw, page)
                except Exception as e:
                    fail_n += 1
                    cs.log(f"collect: {kw} p{page} 오류 {e!r}")
                    break
                outcome = r["outcome"]
                if outcome == "budget":
                    cs.log(f"collect: 공통 예산 소진 — 중단")
                    stopped = "budget"
                    break
                if outcome == "paused":
                    cs.log(f"collect: 공통 중단 상태({r.get('remaining', 0):.0f}s) — 중단")
                    stopped = "paused"
                    break
                if outcome in ("ok", "no_results"):
                    html = r["html"]
                    rows = sr.parse(html) if outcome == "ok" else []
                    counts: dict = {}
                    for row in rows:
                        counts[row["badge"]] = counts.get(row["badge"], 0) + 1
                    res = {"query": kw, "page": page, "run_id": st.get("run_id"), "outcome": outcome,
                           "http_status": r.get("http_status"), "count": len(rows),
                           "total_count": sr.parse_total_count(html) if outcome == "ok" else 0,
                           "badge_counts": counts, "ads": sum(row["ad"] for row in rows),
                           "related_keywords": sr.parse_related_keywords(html) if (outcome == "ok" and page == 1) else [],
                           "autocomplete": autocomplete(kw) if (outcome == "ok" and page == 1) else [],
                           "items": rows}
                    save_result(res)
                    if outcome == "ok":
                        ok_n += 1
                        cs.log(f"collect: {kw} p{page} ok count={len(rows)} ads={res['ads']}")
                    else:
                        empty_n += 1
                        cs.log(f"collect: {kw} p{page} no_results(정상 무결과)")
                    break
                if outcome in ("challenge", "http_error"):
                    st["last_block"] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                        "outcome": outcome, "http_status": r.get("http_status"), "query": kw}
                    if allow_reissue and on_block and on_block(kw, page, outcome, attempt):
                        time.sleep(random.uniform(GAP_MIN, GAP_MAX))
                        continue
                    fail_n += 1
                    _record_failure(kw, page, r, st.get("run_id"))
                    # 공통 중단을 건다 — 수집기뿐 아니라 헬스체크·온디맨드도 이 동안 새 검색을 멈춘다.
                    cs.maybe_pause_on_block(outcome, r.get("http_status"), reason=f"collect {kw}")
                    cs.log(f"collect: {kw} p{page} {outcome} — 공통 중단 판정")
                    stopped = f"blocked:{outcome}"
                    break
                # load_error 등: 실패로 기록하되 backoff는 걸지 않음(일시적 네트워크·로딩 문제)
                fail_n += 1
                cs.log(f"collect: {kw} p{page} {outcome}")
                _record_failure(kw, page, r, st.get("run_id"))
                break
            write_state(st)
            if stopped:
                break
        if stopped:
            break
    return ok_n, fail_n, empty_n, stopped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="요청 없이 순서만 확인")
    ap.add_argument("--limit", type=int, help="앞에서 N개 검색어만")
    ap.add_argument("--status", action="store_true", help="상태만 출력")
    a = ap.parse_args()
    if a.status:
        st = read_state()
        h = cs.read_health()
        ctrl = cs.read_control()
        # 데이터 신선도: runs.jsonl 마지막 정상 조회(ok) 시각
        last_ok = None
        runs = DATA / "runs.jsonl"
        if runs.exists():
            for line in reversed(runs.read_text(encoding="utf-8").splitlines()):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("outcome", "ok") == "ok":
                    last_ok = row.get("ts")
                    break
        print(json.dumps({
            "control": {"paused_remaining_sec": round(cs.paused_remaining()),
                        "reason": ctrl.get("reason", ""), "counts_today": (ctrl.get("counts", {}) if ctrl.get("day") == cs._today() else {}),
                        "budget": cs.DAILY_BUDGET},
            "health": {k: h.get(k) for k in ("ok", "outcome", "fails", "ts")},
            "last_result": st.get("last_result"),
            "last_ok_collected": last_ok,
        }, ensure_ascii=False, indent=1))
        return 0
    return run(dry=a.dry, limit=a.limit)


if __name__ == "__main__":
    sys.exit(main())
