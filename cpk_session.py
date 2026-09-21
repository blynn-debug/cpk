"""쿠팡 검색용 쿠키 세션 유지 모듈.

브라우저에서 복사한 Cookie 헤더를 쿠키통(jar.json)으로 저장하고,
주기적으로 쿠팡 홈을 GET 해서 Set-Cookie로 재발급되는 아카마이 쿠키를 병합한다.
쿠키는 상태 폴더(CPK_HOME, 기본 ~/cpk/state) 밖으로 내보내지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from http.cookiejar import Cookie
from pathlib import Path

from curl_cffi import requests

HOME = Path(os.environ.get("CPK_HOME", Path.home() / "cpk" / "state"))
JAR = HOME / "jar.json"
HEALTH = HOME / "health.json"
LOG = HOME / "keepalive.log"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
               "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "accept-language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "max-age=0",
    "priority": "u=0, i",
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": UA,
}
HOME_URL = "https://www.coupang.com/"
SEARCH_URL = "https://www.coupang.com/np/search?q={q}&channel=user"
AKAMAI = ("_abck", "bm_sz", "bm_sv", "ak_bmsc", "bm_s", "bm_so", "bm_lso")


def log(msg: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line)
    except UnicodeEncodeError:
        # 콘솔 인코딩이 utf-8이 아니어도(예: Windows cp949) 로깅이 죽지 않게 한다.
        enc = (sys.stdout.encoding or "utf-8")
        print(line.encode(enc, "replace").decode(enc, "replace"))


# ---------- 쿠키통 저장/복원 ----------

def _cookie_to_dict(c: Cookie) -> dict:
    return {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
            "expires": c.expires, "secure": c.secure}


def _dict_to_cookie(d: dict) -> Cookie:
    return Cookie(version=0, name=d["name"], value=d["value"], port=None, port_specified=False,
                  domain=d.get("domain") or ".coupang.com", domain_specified=True,
                  domain_initial_dot=str(d.get("domain", "")).startswith("."),
                  path=d.get("path") or "/", path_specified=True, secure=bool(d.get("secure")),
                  expires=d.get("expires"), discard=False, comment=None, comment_url=None, rest={})


def load_jar() -> list[dict]:
    if not JAR.exists():
        return []
    return json.loads(JAR.read_text(encoding="utf-8"))


def save_jar(cookies: list[dict]) -> None:
    atomic_write(JAR, json.dumps(cookies, ensure_ascii=False, indent=1))
    try:
        os.chmod(JAR, 0o600)
    except OSError:
        pass


# 로그인 상태를 나타내는 쿠키. 검색 조회에는 필요 없고 계정 위험만 키우므로 기본적으로 뺀다.
LOGIN_COOKIES = {"member_srl", "ILOGIN", "CT_AT", "CT_LSID", "CSID", "CUPT", "CPUSR_RL", "gd1", "APPVER"}


def import_cookie_header(header: str, keep_login: bool = False) -> int:
    """DevTools의 Cookie 요청 헤더 문자열 한 줄을 쿠키통으로 저장. 저장된 개수 반환.

    keep_login=False 이면 LOGIN_COOKIES 를 버린다(비로그인 세션으로 동작).
    """
    header = header.strip().removeprefix("cookie:").removeprefix("Cookie:").strip()
    cookies = []
    for kv in header.split(";"):
        kv = kv.strip()
        if "=" not in kv:
            continue
        name, value = kv.split("=", 1)
        name = name.strip()
        if not keep_login and name in LOGIN_COOKIES:
            continue
        cookies.append({"name": name, "value": value.strip(), "domain": ".coupang.com",
                        "path": "/", "expires": None, "secure": True})
    if not cookies:
        raise ValueError("쿠키가 하나도 파싱되지 않았습니다")
    save_jar(cookies)
    return len(cookies)


# ---------- 세션 ----------

def make_session() -> requests.Session:
    # CPK_PROXY=http://user:pass@host:port 를 주면 모든 요청을 그 프록시로 보낸다(주거용 IP 우회용).
    proxy = os.environ.get("CPK_PROXY", "").strip() or None
    s = requests.Session(impersonate="chrome", proxies={"https": proxy, "http": proxy} if proxy else None)
    for d in load_jar():
        s.cookies.jar.set_cookie(_dict_to_cookie(d))
    return s


def persist(s: requests.Session) -> list[str]:
    """세션의 현재 쿠키를 저장하고 이름 목록을 반환."""
    cookies = [_cookie_to_dict(c) for c in s.cookies.jar]
    # 같은 이름이 여러 도메인/경로로 중복되면 마지막 것만 유지
    dedup: dict[str, dict] = {}
    for c in cookies:
        dedup[c["name"]] = c
    save_jar(list(dedup.values()))
    return sorted(dedup)


_UA_OVERRIDE: dict | None = None


def ua_override() -> dict:
    """state/ua.json 이 있으면(브라우저 재발급 시 기록) 그 UA·플랫폼을 요청 헤더에 덮어쓴다."""
    global _UA_OVERRIDE
    if _UA_OVERRIDE is None:
        f = HOME / "ua.json"
        try:
            _UA_OVERRIDE = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except Exception:
            _UA_OVERRIDE = {}
    return _UA_OVERRIDE


def reset_ua_override() -> None:
    global _UA_OVERRIDE
    _UA_OVERRIDE = None


def get(s: requests.Session, url: str, referer: str = HOME_URL, timeout: int = 40):
    h = dict(BASE_HEADERS)
    h.update(ua_override())
    h["referer"] = referer
    return s.get(url, headers=h, timeout=timeout)


def refresh(s: requests.Session | None = None) -> dict:
    """쿠팡 홈 GET → 재발급된 쿠키 병합. 결과 요약 dict 반환."""
    s = s or make_session()
    before = {c.name: c.value for c in s.cookies.jar}
    r = get(s, HOME_URL, referer="https://www.coupang.com/np/search?q=&channel=user")
    after = {c.name: c.value for c in s.cookies.jar}
    changed = sorted(n for n in after if before.get(n) != after[n])
    persist(s)
    return {"status": r.status_code, "bytes": len(r.content), "reissued": changed,
            "akamai_present": [n for n in AKAMAI if n in after]}


MIN_GAP_SEC = float(os.environ.get("CPK_MIN_GAP_SEC", "4"))
_last_page_fetch = 0.0


def _throttle() -> None:
    """검색 페이지 요청 사이에 최소 간격을 둔다(아카마이 행동 챌린지 예방)."""
    global _last_page_fetch
    wait = MIN_GAP_SEC - (time.monotonic() - _last_page_fetch)
    if wait > 0:
        time.sleep(wait)
    _last_page_fetch = time.monotonic()


def is_challenge(html: str) -> bool:
    """접근이 막힌 화면인지 — 아카마이 행동 검증(sec-cpt) 또는 쿠팡 접근 제한(error403) 템플릿.
    2026-09-17 실측: 검색이 상품 대신 error403 안내 HTML을 받는데 이를 못 잡아 load_error 로 오판했다."""
    h = html or ""
    return ('sec-if-cpt-container' in h or 'sec-bc-tile' in h
            or 'id="error403"' in h or '사용권한이' in h)


def search_html(s: requests.Session, query: str, page: int = 1) -> tuple[int, str]:
    url = SEARCH_URL.format(q=urllib.parse.quote(query))
    if page > 1:
        url += f"&page={page}"
    _throttle()
    r = get(s, url)
    persist(s)
    if r.status_code == 200 and is_challenge(r.text):
        log(f"검색 페이지가 아카마이 챌린지로 응답 (q={query!r}) — 쿠키 재주입 또는 대기 필요")
        return 429, r.text
    return r.status_code, r.text


def units_in(html: str) -> int:
    return html.count("ProductUnit_productUnit")


AUTOCOMPLETE_URL = "https://www.coupang.com/n-api/web-adapter/search?keyword={q}&_={ts}"


def autocomplete(s: requests.Session, keyword: str, referer: str | None = None,
                 *, timeout: float = 25, persist_cookies: bool = True) -> dict:
    """검색창 자동완성. 브라우저와 같은 XHR 헤더로 호출한다.

    반환: {"items": [{"keyword","travel"}], "status": int|None, "ok": bool}.
    ok 는 HTTP 조회 성공 여부다 — 정상 빈 결과(ok=True, items=[])와 조회 실패(ok=False)를 구분한다.
    """
    h = dict(BASE_HEADERS)
    for k in ("cache-control", "upgrade-insecure-requests", "sec-fetch-user", "priority"):
        h.pop(k, None)
    h.update({
        "accept": "application/json, text/javascript, */*; q=0.01",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "x-requested-with": "XMLHttpRequest",
        "priority": "u=1, i",
        "referer": referer or SEARCH_URL.format(q=urllib.parse.quote(keyword)),
    })
    url = AUTOCOMPLETE_URL.format(q=urllib.parse.quote(keyword), ts=int(time.time() * 1000))
    r = s.get(url, headers=h, timeout=timeout)
    if persist_cookies:
        persist(s)
    if r.status_code != 200:
        return {"items": [], "status": r.status_code, "ok": False}
    try:
        data = r.json()
    except ValueError:
        return {"items": [], "status": r.status_code, "ok": False}
    if not isinstance(data, list):
        return {"items": [], "status": r.status_code, "ok": False}
    out = []
    for it in data:
        if not isinstance(it, dict):
            return {"items": [], "status": r.status_code, "ok": False}
        kw = (it or {}).get("keyword")
        if kw:
            out.append({"keyword": kw, "travel": bool(it.get("travelKeyword"))})
    return {"items": out, "status": 200, "ok": True}


def autocomplete_keywords(query: str, s: "requests.Session | None" = None) -> dict:
    """자동완성 검색어를 공용으로 조회한다. 모든 경로(검색·수집·키워드 CLI)가 이 함수를 쓴다.
    반환: {"ok": 조회 성공 여부, "items": [키워드], "used": 실제 조회에 쓴 검색어}.
    조회 실패(예외)와 정상 빈 결과를 구분한다. 서너 단어짜리 긴 검색어는 앞 단어를 떼어내며 재시도한다."""
    s = s or make_session()

    def _ac(q):
        # 자동완성도 공통 관문을 지난다 — 중단·예산·간격이 검색과 동일하게 적용된다.
        # ok 는 HTTP 조회 성공 여부(403 등은 실패). 명확한 차단(403/429)이면 공통 중단을 건다.
        try:
            with request_gate("autocomplete"):
                res = autocomplete(s, q)
                if not res["ok"]:
                    maybe_pause_on_block("http_error", res.get("status"), reason="autocomplete")
                return res["items"], res["ok"]
        except (RequestPaused, BudgetExceeded):
            raise
        except Exception:
            return [], False

    try:
        items, ok = _ac(query)
        used = query
        if ok and not items:
            words = query.split()
            if len(words) > 2:
                for cand in [" ".join(words[i:]) for i in range(1, len(words) - 1)] + [" ".join(words[:2])]:
                    c, cok = _ac(cand)
                    if c:
                        items, used, ok = c, cand, cok
                        break
    except (RequestPaused, BudgetExceeded):
        # 중단·예산이면 자동완성 요청을 보내지 않는다.
        return {"ok": False, "items": [], "used": query, "blocked": True}
    return {"ok": ok, "items": [a["keyword"] for a in items], "used": used}


def canary(s: requests.Session | None = None, query: str = "계란보관함") -> dict:
    s = s or make_session()
    status, html = search_html(s, query)
    return {"status": status, "units": units_in(html), "query": query}


import contextlib


@contextlib.contextmanager
def _flock(name: str, timeout: float = 120.0, blocking: bool = True, *, wait=None):
    """이름 붙은 파일 잠금. blocking=True면 timeout까지 기다리다 TimeoutError,
    blocking=False면 즉시 획득 가능 여부(bool)를 yield 한다. Windows(fcntl 없음)에서는 항상 획득."""
    HOME.mkdir(parents=True, exist_ok=True)
    lock_path = HOME / name
    f = open(lock_path, "a+")
    try:
        try:
            import fcntl
        except ImportError:
            yield True
            return
        t0 = time.monotonic()
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not blocking:
                    yield False
                    return
                left = timeout - (time.monotonic() - t0)
                if left <= 0:
                    raise TimeoutError(f"{name} 잠금 대기 {timeout}s 초과: {lock_path}")
                (wait or time.sleep)(min(0.05, left))
        try:
            yield True
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def jar_lock(timeout: float = 120.0):
    """쿠키통을 쓰는 작업(keepalive·collect)이 겹치지 않게 하는 잠금."""
    return _flock("lock", timeout)


def browser_lock(timeout: float = 0.0, blocking: bool = False):
    """Chrome 실행·재발급을 한 번에 하나만 하도록 하는 잠금.
    기본은 non-blocking: 이미 다른 잡이 재발급 중이면 False 를 yield 한다(중복 실행 방지)."""
    return _flock("browser.lock", timeout, blocking=blocking)


# 검색을 프로세스 간 직렬화하고 최소 간격을 강제한다(아카마이가 IP를 태우지 않게 하는 핵심 안전장치).
SEARCH_MIN_GAP = float(os.environ.get("CPK_SEARCH_MIN_GAP", "20"))


@contextlib.contextmanager
def search_gate(min_gap: float | None = None, timeout: float = 600.0, *, wait=None):
    """검색 한 건을 감싼다. 잠금으로 동시 검색을 막고, 직전 검색과의 간격이 min_gap 미만이면 기다린다.
    온디맨드·수집·헬스체크가 서로 다른 프로세스여도 파일 잠금·타임스탬프로 공유된다."""
    gap = SEARCH_MIN_GAP if min_gap is None else min_gap
    deadline = time.monotonic() + timeout
    with _flock("search.lock", timeout=timeout, blocking=True, wait=wait):
        stamp = HOME / "last_search"
        try:
            last = float(stamp.read_text())
        except Exception:
            last = 0.0
        delay = gap - (time.time() - last)
        if delay > 0:
            if delay >= deadline - time.monotonic():
                raise TimeoutError("search gap exceeds request deadline")
            (wait or time.sleep)(delay)
        try:
            yield
        finally:
            try:
                stamp.write_text(str(time.time()))
            except Exception:
                pass


# ---------- 공통 요청 제어 (모든 경로가 공유하는 중단 상태·예산) ----------
# control.json: {paused_until, reason, day, counts:{kind:n}}
DAILY_BUDGET = int(os.environ.get("CPK_DAILY_BUDGET", "300"))


class RequestPaused(Exception):
    """중단 상태라 새 요청을 시작하면 안 됨. remaining=남은 초, reason=사유."""
    def __init__(self, remaining: float, reason: str = ""):
        super().__init__(f"paused {remaining:.0f}s ({reason})")
        self.remaining = remaining
        self.reason = reason


class BudgetExceeded(Exception):
    """오늘의 요청 예산 소진."""


def _control_path():
    return HOME / "control.json"


def read_control() -> dict:
    p = _control_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_control(c: dict) -> None:
    atomic_write(_control_path(), json.dumps(c, ensure_ascii=False, indent=1))


def pause(seconds: float, reason: str = "") -> None:
    """모든 CPK 경로가 공유하는 중단을 건다. 이미 더 늦은 중단이 있으면 늘리지 않는다."""
    with _flock("control.lock", timeout=30, blocking=True):
        c = read_control()
        until = time.time() + seconds
        if until > c.get("paused_until", 0):
            c["paused_until"] = until
            c["reason"] = reason
            _write_control(c)


def clear_pause() -> None:
    with _flock("control.lock", timeout=30, blocking=True):
        c = read_control()
        c["paused_until"] = 0
        c["reason"] = ""
        _write_control(c)


def paused_remaining() -> float:
    return max(0.0, read_control().get("paused_until", 0) - time.time())


def maybe_pause_on_block(outcome: str, http_status=None, reason: str = "") -> bool:
    """명확한 차단(봇 검증 챌린지, HTTP 403/429)이면 공통 중단을 건다. 그 외 오류는 중단하지 않는다.
    모든 경로(검색·헬스·수집·재발급)가 차단을 만났을 때 공용으로 호출해 온디맨드까지 함께 쉬게 한다.
    pause 는 더 늦은 것만 반영하므로 여러 번 호출해도 안전하다. 중단을 걸었으면 True 반환."""
    blocked = outcome == "challenge" or (outcome == "http_error" and http_status in (403, 429))
    if blocked:
        hours = float(os.environ.get("CPK_BACKOFF_HOURS", "6"))
        pause(hours * 3600, reason=reason or f"{outcome} {http_status}")
    return blocked


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def today_count() -> int:
    """오늘(날짜 기준) 요청 총합. 날짜가 바뀌었으면 어제 집계는 0으로 본다(전날 예산이 오늘을 막지 않게)."""
    c = read_control()
    if c.get("day") != _today():
        return 0
    return sum(c.get("counts", {}).values())


def admit_request(kind: str, limit: "int | None" = None, *, timeout: float = 30) -> int:
    """control.lock 단일 구간에서 중단 확인·날짜 갱신·예산 확인·집계를 원자적으로 수행한다.
    중단이면 RequestPaused, 예산 초과면 BudgetExceeded(둘 다 집계하지 않음). 성공 시 반영 후 총합 반환.
    잠금 대기 중 다른 작업이 중단을 걸어도, 이 잠금을 잡은 뒤 최신 상태로 확인하므로 놓치지 않는다."""
    with _flock("control.lock", timeout=timeout, blocking=True):
        c = read_control()
        if c.get("paused_until", 0) > time.time():
            raise RequestPaused(c["paused_until"] - time.time(), c.get("reason", ""))
        if c.get("day") != _today():
            c["day"] = _today()
            c["counts"] = {}
        counts = c.setdefault("counts", {})
        total = sum(counts.values())
        if limit is not None and total >= limit:
            raise BudgetExceeded(f"{total}/{limit}")
        counts[kind] = counts.get(kind, 0) + 1
        _write_control(c)
        return total + 1


@contextlib.contextmanager
def request_gate(kind: str, min_gap: float | None = None, budget: bool = True):
    """모든 실제 요청(검색·워밍업·자동완성·재발급 페이지 이동)이 지나야 하는 공통 관문.
      1) 먼저 잠금·최소 간격을 기다린다(search_gate).
      2) 그 뒤 admit_request 가 control.lock 안에서 중단·날짜·예산·집계를 원자적으로 처리한다.
    이로써 대기 중 걸린 중단을 놓치지 않고, 보내지 않을 요청(중단·예산)은 집계되지 않는다."""
    with search_gate(min_gap):
        admit_request(kind, limit=DAILY_BUDGET if budget else None)
        yield


def atomic_write(path, text: str) -> None:
    """임시 파일에 쓰고 os.replace 로 교체한다. 중단·동시 접근에도 부분 파일이 남지 않는다.
    임시 파일 이름에 pid+난수를 붙여 여러 작성자가 동시에 써도 서로 덮지 않는다."""
    import uuid
    from pathlib import Path as _P
    path = _P(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def append_line(path, line: str, *, timeout: float = 30) -> None:
    """append 전용 파일(runs.jsonl 등)에 한 줄을 잠금 하에 덧붙인다(동시 기록으로 줄이 섞이지 않게)."""
    from pathlib import Path as _P
    path = _P(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _flock("append.lock", timeout=timeout, blocking=True):
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def write_health(d: dict) -> None:
    d["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    atomic_write(HEALTH, json.dumps(d, ensure_ascii=False, indent=1))


def update_health(mutate) -> dict:
    """health.lock 안에서 최신 health 를 읽어 mutate(h)->h 로 바꾸고 저장한다.
    동시 헬스체크가 같은 이전 상태를 읽어 카운터를 덮어쓰는 유실을 막는다."""
    with _flock("health.lock", timeout=30, blocking=True):
        h = read_health()
        h = mutate(h) or h
        write_health(h)
        return h


def read_health() -> dict:
    return json.loads(HEALTH.read_text(encoding="utf-8")) if HEALTH.exists() else {}
