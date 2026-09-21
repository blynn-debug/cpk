"""맥미니의 실제 Chrome으로 쿠팡 세션을 새로 발급받아 쿠키통에 넣는다(무인 재발급).

원리: 일반 Chrome을 전용 프로필 + 디버그 포트만 켜서 띄우고(자동화 플래그 없음),
CDP로 탭 하나를 열어 쿠팡 홈 → 검색 한 번을 실제 브라우저가 수행하게 한 뒤
브라우저의 쿠키 전체를 읽어 cpk_session 쿠키통으로 저장한다. 로그인 쿠키는 버린다.
브라우저 UA도 함께 기록해(ua.json) 파이썬 요청이 같은 UA를 쓰게 한다.

사용:  python cpk_browser.py [--query 검색어] [--keep-chrome] [--verify]
환경:  CPK_HOME(상태 폴더), CPK_CHROME(실행 파일), CPK_CDP_PORT(기본 9222)
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import cpk_session as cs

CHROME = os.environ.get("CPK_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PORT = int(os.environ.get("CPK_CDP_PORT", "9222"))
# url: 검색 URL로 직접 이동(기본). ui: 홈에서 검색창에 입력·제출(사용자 흐름, error403 회피 실험용).
SEARCH_MODE = os.environ.get("CPK_SEARCH_MODE", "url").strip()
# 1이면 검색마다 깨끗한 시크릿 컨텍스트를 새로 열어 개인화 오염을 막는다(홈 워밍업 강제).
FRESH_CONTEXT = os.environ.get("CPK_FRESH_CONTEXT", "0").strip() in ("1", "true", "on", "yes")
# 대역폭 절약: 프록시 경로에서 이미지·미디어·폰트를 차단한다(파싱엔 HTML만 필요). 0이면 끄기.
PROXY_BLOCK_MEDIA = os.environ.get("CPK_PROXY_BLOCK_MEDIA", "1").strip() in ("1", "true", "on", "yes")
# sticky 세션: 검색(컨텍스트) 하나 동안 같은 출구 IP를 유지. 0이면 회전형 그대로.
# Proxy-Cheap 형식: 비밀번호에 _session-<id>_ttl-<분> 부가(둘 다 있어야 함). 컨텍스트마다 id가 달라 검색 간 IP는 바뀐다.
PROXY_STICKY = os.environ.get("CPK_PROXY_STICKY", "1").strip() in ("1", "true", "on", "yes")
PROXY_TTL = int(os.environ.get("CPK_PROXY_TTL", "10"))  # sticky 세션 유지 시간(분)
# 차가운 컨텍스트(시크릿/프록시)에서 검색 전 홈에서 _abck 센서를 데우는 최대 대기(초). 0이면 워밍 생략.
WARM_SECS = float(os.environ.get("CPK_WARM_SECS", "12"))
BLOCK_URL_PATTERNS = ["*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.svg", "*.ico",
                      "*.mp4", "*.webm", "*.woff", "*.woff2", "*.ttf", "*.otf",
                      "*coupangcdn.com/*image*", "*thumbnail*"]


def proxy_config():
    """CPK_PROXY 를 Chrome 컨텍스트용 설정으로 파싱한다. 없으면 None.
    형식: scheme://user:pass@host:port  또는  host:port (+ CPK_PROXY_USER/PASS).
    반환: {"server": "scheme://host:port", "user": .., "pass": ..}. server 엔 자격증명이 없다
    (Chrome --proxy-server/컨텍스트 proxyServer 는 인라인 자격증명을 못 받는다 → 인증은 CDP로 처리)."""
    raw = os.environ.get("CPK_PROXY", "").strip()
    if not raw:
        return None
    from urllib.parse import urlparse
    u = urlparse(raw if "://" in raw else "http://" + raw)
    if not u.hostname:
        return None
    scheme = u.scheme or "http"
    server = f"{scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    user = u.username or os.environ.get("CPK_PROXY_USER", "").strip()
    pw = u.password or os.environ.get("CPK_PROXY_PASS", "").strip()
    return {"server": server, "user": user or "", "pass": pw or ""}


PROXY = proxy_config()
# 프록시가 있으면 --proxy-server로 띄운 전용 Chrome(별도 포트·프로필)을 쓴다.
# (컨텍스트 단위 proxyServer는 Chrome이 무시하는 경우가 있어 브라우저 단위 플래그로 강제한다.)
# 상시 Chrome(9222)은 직결로 그대로 두고, 검색만 이 프록시 인스턴스로 돌린다.
if PROXY:
    PORT = int(os.environ.get("CPK_PROXY_CDP_PORT", "9223"))
    PROFILE = cs.HOME / os.environ.get("CPK_PROXY_PROFILE", "chrome-profile-proxy")
else:
    PROFILE = cs.HOME / "chrome-profile"
UA_FILE = cs.HOME / "ua.json"
BASE = f"http://127.0.0.1:{PORT}"


def _http(method: str, path: str, timeout: float = 5) -> dict | list | None:
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
        return json.loads(body) if body.strip() else None


def chrome_alive(timeout: float = 2) -> bool:
    try:
        return bool(_http("GET", "/json/version", timeout))
    except Exception:
        return False


def launch_chrome(*, deadline: float | None = None) -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1280,900",
            "--lang=ko-KR"]
    if PROXY and PROXY.get("server"):
        # 브라우저 단위 프록시(컨텍스트 단위는 무시되는 경우가 있음). 로컬·CDP는 우회.
        args += [f"--proxy-server={PROXY['server']}", "--proxy-bypass-list=<-loopback>"]
    args.append("about:blank")
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    end = min(time.monotonic() + 20, deadline) if deadline is not None else time.monotonic() + 20
    while time.monotonic() < end:
        if chrome_alive(timeout=max(0.01, min(2, end - time.monotonic()))):
            return
        time.sleep(max(0, min(0.5, end - time.monotonic())))
    raise TimeoutError("Chrome 디버그 포트가 열리지 않음")


def open_incognito_tab(proxy: dict | None = None, *, deadline: float | None = None):
    """새 격리(incognito) 브라우저 컨텍스트와 그 안의 탭을 연다. 쿠키·이력이 깨끗하다.
    proxy 를 주면 그 컨텍스트만 해당 프록시로 라우팅한다(상시 Chrome 본체는 직결 유지).
    proxy 에 자격증명이 있으면 CDP Fetch 로 프록시 인증을 처리한다.
    반환: (tab, close_fn). close_fn() 이 탭과 컨텍스트를 함께 정리한다."""
    import websocket
    def remaining(limit=5):
        seconds = min(limit, deadline - time.monotonic()) if deadline is not None else limit
        if seconds <= 0:
            raise TimeoutError("context deadline exceeded")
        return seconds

    ver = _http("GET", "/json/version", timeout=remaining())
    bws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=remaining(), suppress_origin=True)
    box = {"seq": 0}

    def bcall(method, **p):
        box["seq"] += 1
        bws.send(json.dumps({"id": box["seq"], "method": method, "params": p}))
        end = time.monotonic() + (3 if method == "Target.disposeBrowserContext" else remaining())
        while True:
            if time.monotonic() >= end:
                raise TimeoutError("context command deadline exceeded")
            bws.settimeout(end - time.monotonic())
            m = json.loads(bws.recv())
            if m.get("id") == box["seq"]:
                if "error" in m:
                    raise RuntimeError("context command failed")
                return m.get("result", {})

    ctx_id = None
    tab = None
    try:
        ctx_params = {"disposeOnDetach": True}
        if proxy and proxy.get("server"):
            ctx_params["proxyServer"] = proxy["server"]   # 이 컨텍스트만 프록시 경유
        ctx_id = bcall("Target.createBrowserContext", **ctx_params)["browserContextId"]
        tid = bcall("Target.createTarget", url="about:blank", browserContextId=ctx_id)["targetId"]
        tab = Tab.__new__(Tab)
        tab.id = tid
        tab.seq = 0
        tab.last_status = tab.last_error = tab._loader = None
        tab._fire_id = 0
        tab._proxy_auth = None
        tab._block_media = False
        tab.deadline = deadline
        tab.ws = websocket.create_connection(f"{BASE.replace('http://', 'ws://')}/devtools/page/{tid}",
                                             timeout=remaining(), suppress_origin=True)
        tab.call("Page.enable")
        tab.call("Network.enable")
        if proxy:
            need_auth = bool(proxy.get("user") or proxy.get("pass"))
            if need_auth:
                # 프록시 인증 + (선택)미디어 차단을 Fetch 인터셉트로 처리한다.
                # sticky: 비밀번호에 컨텍스트별 세션 id를 붙여 이 검색 동안 같은 출구 IP를 유지한다
                # (회전형이 홈↔검색 사이에 IP를 바꿔 아카마이 챌린지를 유발하는 문제 방지).
                # 서로 다른 세션 id가 서로 다른 출구 IP를 보장하지는 않는다.
                pw = proxy.get("pass", "")
                if pw and PROXY_STICKY:
                    pw = f"{pw}_session-{random.randrange(16 ** 10):08x}_ttl-{PROXY_TTL}"
                tab._proxy_auth = (proxy.get("user", ""), pw)
                tab._block_media = PROXY_BLOCK_MEDIA
                tab.call("Fetch.enable", handleAuthRequests=True, patterns=[{"urlPattern": "*"}])
            elif PROXY_BLOCK_MEDIA:
                # 자격증명 없음(IP 화이트리스트): Fetch 없이 네트워크 계층에서 미디어만 차단.
                try:
                    tab.call("Network.setBlockedURLs", urls=BLOCK_URL_PATTERNS)
                except Exception:
                    pass

    except Exception:
        if tab is not None and getattr(tab, "ws", None) is not None:
            tab.ws.close()
        if ctx_id is not None:
            with contextlib.suppress(Exception):
                bcall("Target.disposeBrowserContext", browserContextId=ctx_id)
        bws.close()
        raise

    def close_fn():
        try:
            tab.ws.close()
        finally:
            try:
                bcall("Target.disposeBrowserContext", browserContextId=ctx_id)
            finally:
                bws.close()
    return tab, close_fn


def _abck_validated(tab) -> bool:
    """아카마이 _abck 센서 쿠키가 '검증됨' 상태인지. 값의 '~' 두번째 필드가 -1이 아니면 검증됨."""
    try:
        for c in tab.cookies():
            if c.get("name") == "_abck":
                parts = (c.get("value") or "").split("~")
                return len(parts) > 1 and parts[1] != "-1"
    except Exception:
        pass
    return False


def warm_context(tab, max_wait: float = None) -> bool:
    """정해진 워밍 시간 동안 이벤트를 처리하고 실제 검색 입력의 준비를 확인한다.

    쿠키 내부 필드를 성공의 증거로 사용하지 않는다.
    최종 성공 여부는 검색 페이지의 실제 데이터로 판정한다.
    """
    if max_wait is None:
        max_wait = WARM_SECS
    # Live pilot: an input appearing after 0.5s did not establish session readiness.
    # Keep the configured cold-start allowance until a controlled trial supports reducing it.
    end = time.monotonic() + max(0, max_wait)
    while time.monotonic() < end:
        # Preserve the established browser warmup interaction; changing it together
        # with transport timing would confound success-rate comparisons.
        tab.call("Input.dispatchMouseEvent", type="mouseMoved", x=400, y=300)
        tab.call("Input.dispatchMouseEvent", type="mouseMoved", x=660, y=520)
        tab.eval("window.scrollTo(0, 600)")
        tab.pump(max(0, min(1.2, end - time.monotonic())))
    return bool(tab.eval("document.readyState !== 'loading' && !!document.querySelector("
                         "'input[name=q], #headerSearchKeyword, input[type=search]')"))


class Tab:
    """CDP 웹소켓 한 개."""

    def __init__(self):
        import websocket  # websocket-client
        info = _http("PUT", "/json/new?about:blank")
        self.id = info["id"]
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"], timeout=30,
                                              suppress_origin=True)
        self.seq = 0
        self.last_status = None   # 최근 goto의 메인 문서 HTTP 상태
        self.last_error = None    # 최근 goto의 로딩 실패 사유(없으면 None)
        self._loader = None       # 현재 탐색의 loaderId(메인 문서 응답 매칭용)
        self._fire_id = 0         # 응답을 기다리지 않는 전송용 음수 id(자격증명 인증 등)
        self._proxy_auth = None   # (user, pass) 있으면 Fetch.authRequired 에 응답
        self._block_media = False  # Fetch 경로에서 이미지·미디어 차단 여부
        self.call("Page.enable")
        self.call("Network.enable")

    def pump(self, seconds: float) -> None:
        """지정 시간 동안 들어오는 CDP 이벤트를 읽어 처리한다(그냥 sleep과 달리 Fetch 인터셉트를
        계속 이어줘 프록시 인증·센서 요청이 멈추지 않게 한다)."""
        import websocket

        end = time.monotonic() + max(0, seconds)
        try:
            while time.monotonic() < end:
                self.ws.settimeout(self._remaining(min(0.25, end - time.monotonic())))
                try:
                    msg = json.loads(self.ws.recv())
                except (TimeoutError, websocket.WebSocketTimeoutException):
                    continue
                if msg.get("method"):
                    self._handle_event(msg)
        finally:
            self.ws.settimeout(30)

    def _remaining(self, limit: float) -> float:
        """Bound every socket wait by the operation and request deadlines."""
        for deadline in (getattr(self, "deadline", None), getattr(self, "_operation_deadline", None)):
            if deadline is not None:
                limit = min(limit, deadline - time.monotonic())
        if limit <= 0:
            raise TimeoutError("browser deadline exceeded")
        return limit

    def _fire(self, method: str, **params) -> None:
        """응답을 기다리지 않고 명령을 보낸다(음수 id → call()의 양수 seq와 절대 충돌 안 함).
        Fetch.continueRequest/continueWithAuth 처럼 수신 루프 안에서 즉시 보내야 할 때 쓴다."""
        self._fire_id -= 1
        self.ws.send(json.dumps({"id": self._fire_id, "method": method, "params": params}))

    def _handle_event(self, msg: dict) -> None:
        """CDP 이벤트를 수신하는 즉시 처리한다(버리지 않는다).
        메인 문서 응답의 HTTP 상태와 로딩 실패를 어느 지점(call·goto·eval)에서 받아도 반영한다."""
        m = msg.get("method")
        p = msg.get("params", {})
        if m == "Network.responseReceived":
            if p.get("type") == "Document" and (self._loader is None or p.get("loaderId") == self._loader):
                self.last_status = p.get("response", {}).get("status")
                timing = p.get("response", {}).get("timing", {})
                self.last_network = {key: timing[key] for key in
                                     ("proxyStart", "proxyEnd", "dnsStart", "dnsEnd", "connectStart",
                                      "connectEnd", "sslStart", "sslEnd", "sendEnd", "receiveHeadersEnd")
                                     if isinstance(timing.get(key), (int, float))}
        elif m == "Network.loadingFailed":
            if p.get("type") == "Document":
                self.last_error = p.get("errorText") or self.last_error
        elif m == "Fetch.authRequired":
            # 프록시 인증 요구 → 자격증명 제공(없으면 기본 동작).
            u, pw = self._proxy_auth or ("", "")
            resp = ({"response": "ProvideCredentials", "username": u, "password": pw}
                    if (u or pw) else {"response": "Default"})
            self._fire("Fetch.continueWithAuth", requestId=p.get("requestId"), authChallengeResponse=resp)
        elif m == "Fetch.requestPaused":
            # Fetch 활성 시 모든 요청이 여기서 멈춘다 → 계속시키거나(문서·XHR) 미디어면 취소(대역폭 절약).
            rid = p.get("requestId")
            rtype = p.get("resourceType")
            if self._block_media and rtype in ("Image", "Media", "Font"):
                self._fire("Fetch.failRequest", requestId=rid, errorReason="BlockedByClient")
            else:
                self._fire("Fetch.continueRequest", requestId=rid)
        elif m == "Page.loadEventFired":
            self._load_seen = True
        elif m == "Page.domContentEventFired":
            self._dom_seen = True

    def call(self, method: str, **params):
        self.ws.settimeout(self._remaining(30))
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params}))
        end = time.monotonic() + self._remaining(30)
        while True:
            self.ws.settimeout(self._remaining(end - time.monotonic()))
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if msg.get("method"):          # 명령 응답을 기다리는 중 온 이벤트도 즉시 반영
                self._handle_event(msg)

    def goto(self, url: str, settle: tuple[float, float] = (3.0, 6.0), timeout: float = 40,
             *, ready_query: str | None = None, dom_only: bool = False) -> None:
        """페이지를 이동하며 메인 문서의 실제 HTTP 상태(last_status)와 로딩 실패(last_error)를 캡처한다.
        redirect가 있으면 최종 문서 응답의 상태가 남는다. 탐색 자체 오류·타임아웃도 last_error에 남긴다."""
        self.last_status = None
        self.last_network = {}
        self.last_error = None
        self._loader = None
        self._load_seen = self._dom_seen = False
        self.last_ready = ready_query is None
        previous_deadline = getattr(self, "_operation_deadline", None)
        self._operation_deadline = time.monotonic() + self._remaining(timeout)
        try:
            res = self.call("Page.navigate", url=url)
            self._loader = res.get("loaderId")
            if res.get("errorText"):
                self.last_error = res["errorText"]
                return
            self._await_load(timeout, dom_only=dom_only or ready_query is not None)
            if ready_query is not None:
                self.last_ready = self.wait_search_data(ready_query)
            else:
                self.pump(min(random.uniform(*settle), self._remaining(timeout)))
        except Exception:
            if self.last_error is None:
                self.last_error = "transport timeout"
        finally:
            self._operation_deadline = previous_deadline
            self.ws.settimeout(30)

    def _await_load(self, timeout: float = 40, *, dom_only: bool = False) -> None:
        """탐색이 시작된 뒤 loadEventFired 까지 이벤트를 수신·반영하며 기다린다(goto/검색 제출 공용)."""
        end = time.monotonic() + self._remaining(timeout)
        try:
            while not (getattr(self, "_load_seen", False) or (dom_only and getattr(self, "_dom_seen", False))):
                self.ws.settimeout(self._remaining(end - time.monotonic()))
                msg = json.loads(self.ws.recv())
                if msg.get("method"):
                    self._handle_event(msg)
        except Exception:
            if self.last_error is None:
                self.last_error = "transport timeout"
        finally:
            self.ws.settimeout(30)

    def wait_search_data(self, query: str) -> bool:
        """Wait for matching search data to stabilize while continuing Fetch events."""
        previous = None
        stable_since = time.monotonic()
        while True:
            self._remaining(1)
            state = self.eval("JSON.stringify({url:location.href, state:document.readyState, "
                              "units:document.querySelectorAll('[class*=ProductUnit_productUnit]').length, "
                              "text:(document.body?.innerText||'').slice(0,5000), "
                              "related:document.querySelector('[class*=srp_relatedKeywords],.srp-related-keywords')?.innerText||''})")
            data = json.loads(state or "{}")
            text = data.get("text", "")
            if self.last_status and self.last_status >= 400:
                return False
            if cs.is_challenge(text):
                return False
            actual = urllib.parse.parse_qs(urllib.parse.urlsplit(data.get("url", "")).query).get("q", [None])[0]
            no_results = any(s in text for s in ("검색결과가 없습니다", "검색 결과가 없습니다", "검색된 상품이 없습니다"))
            signature = (data.get("units"), data.get("related"), no_results)
            if signature != previous:
                previous, stable_since = signature, time.monotonic()
            if (self.last_status == 200 and actual == query and data.get("state") != "loading"
                    and (data.get("units", 0) or no_results)
                    and time.monotonic() - stable_since >= 0.8):
                return True
            self.pump(0.2)

    SUBMIT_JS = (
        "(function(q){"
        "var e=document.querySelector('input[name=q]')||document.querySelector('#headerSearchKeyword')"
        "||document.querySelector('input[type=search]');"
        "if(!e)return 'no-input';"
        "e.focus();e.value=q;e.dispatchEvent(new Event('input',{bubbles:true}));"
        "var f=e.form;if(f){f.submit();return 'form-submit';}"
        "e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:13,which:13,bubbles:true}));"
        "return 'enter-key';})"
    )

    def submit_search(self, query: str, settle: tuple[float, float] = (3.0, 6.0), timeout: float = 40) -> str:
        """홈의 검색창에 입력하고 폼을 제출해 검색을 일으킨다(사용자 흐름). 결과 문서의 상태를 캡처한다.
        홈에 먼저 이동(goto)해 두어야 한다. 반환: 제출 방식 문자열."""
        self.last_status = None
        self.last_error = None
        self._loader = None   # 폼 제출은 navigate 명령이 아니라 loaderId를 미리 모른다 → 첫 Document 응답을 채택
        self._load_seen = self._dom_seen = False
        import json as _j
        previous_deadline = getattr(self, "_operation_deadline", None)
        self._operation_deadline = time.monotonic() + self._remaining(timeout)
        try:
            how = self.eval(f"{self.SUBMIT_JS}({_j.dumps(query)})")
            self._await_load(timeout)
            self.pump(min(random.uniform(*settle), self._remaining(timeout)))
            return how
        finally:
            self._operation_deadline = previous_deadline
            self.ws.settimeout(30)

    def eval(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    def cookies(self) -> list[dict]:
        return self.call("Network.getAllCookies").get("cookies", [])

    def close(self) -> None:
        try:
            self.ws.close()
        finally:
            try:
                _http("GET", f"/json/close/{self.id}")
            except Exception:
                pass


def quit_chrome() -> None:
    """우리가 띄운 프로필의 Chrome만 종료한다(다른 Chrome 인스턴스는 건드리지 않음)."""
    pat = f"user-data-dir={PROFILE}"  # 앞의 -- 를 빼야 pkill이 옵션으로 오해하지 않음
    subprocess.run(["pkill", "-TERM", "-f", pat], capture_output=True)
    for _ in range(20):
        if subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode != 0:
            return
        time.sleep(0.5)
    subprocess.run(["pkill", "-KILL", "-f", pat], capture_output=True)


PRODUCT_JS = 'document.querySelectorAll("[class*=ProductUnit_productUnit]").length'


def classify_outcome(http_status, html: str, units: int) -> str:
    """검색 시도의 결과 유형. 상품 0개를 무조건 403으로 뭉개지 않고 원인을 구분한다.
      ok          상품 있음
      no_results  정상 응답(200)인데 상품 0개
      challenge   아카마이 봇 검증 화면(_abck 미해결 등)
      http_error  4xx/5xx 응답
      load_error  응답 자체를 못 받음(네트워크/타임아웃)
    """
    if units and units > 0:
        return "ok"
    if cs.is_challenge(html or ""):   # 봇 검증 화면은 200으로도 429로도 오므로 상태코드보다 먼저 판정
        return "challenge"
    if http_status is not None and http_status >= 400:
        return "http_error"
    if http_status == 200:
        return "no_results"
    return "load_error"


def _save_evidence(query: str, page: int, r: dict, keep: int = 30) -> None:
    """실패한 검색의 근거(상태·URL·제목·HTML 일부)를 남겨 사후 진단을 가능하게 한다."""
    try:
        d = cs.HOME / "evidence"
        d.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "query": query, "page": page,
               "outcome": r.get("outcome"), "http_status": r.get("http_status"),
               "error": r.get("error"), "final_url": r.get("final_url"),
               "title": r.get("title"), "product_count": r.get("product_count"),
               "html_head": (r.get("html") or "")[:20000]}
        (d / f"{ts}_{r.get('outcome')}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        files = sorted(d.glob("*.json"))
        for old in files[:-keep]:
            old.unlink(missing_ok=True)
    except Exception as e:
        cs.log(f"evidence 저장 실패: {e!r}")


def harvest(query: str, search_tries: int = 3, kind: str = "reissue") -> dict:
    """홈 → 검색. 각 페이지 이동은 공통 관문을 통과한다(중단·예산·간격 적용). 중단·예산이면 즉시 멈춘다."""
    tab = Tab()
    title1 = title2 = ua = ""
    units = 0
    cookies: list = []
    blocked = None
    try:
        try:
            with cs.request_gate(kind):
                tab.goto(cs.HOME_URL)
            title1 = tab.eval("document.title")
            url = cs.SEARCH_URL.format(q=urllib.parse.quote(query))
            for i in range(search_tries):
                with cs.request_gate(kind):
                    tab.goto(url, settle=(3.0, 5.0) if i == 0 else (5.0, 8.0))
                units = tab.eval(PRODUCT_JS) or 0
                if units > 0:
                    break
            # 재시도 후에도 상품 0이면 결과를 분류해 명확한 차단이면 공통 중단(재발급 경로도 전파).
            if units == 0:
                html = tab.eval("document.documentElement.outerHTML") or ""
                cs.maybe_pause_on_block(classify_outcome(tab.last_status, html, 0),
                                        tab.last_status, reason=f"harvest {kind}")
            title2 = tab.eval("document.title")
            ua = tab.eval("navigator.userAgent")
            cookies = [c for c in tab.cookies() if "coupang.com" in c.get("domain", "")]
        except (cs.RequestPaused, cs.BudgetExceeded) as e:
            blocked = type(e).__name__
    finally:
        tab.close()
    return {"title_home": title1, "title_search": title2, "browser_units": units,
            "ua": ua, "cookies": cookies, "blocked": blocked}


@contextlib.contextmanager
def search_session(kind: str = "ondemand", warmup: bool = True):
    """브라우저로 검색을 여러 번 하기 위한 세션. browser_lock 을 잡고 Chrome 을 띄운 뒤
    탭 하나를 열어 홈으로 워밍업한다. 끝나면 탭을 닫고(우리가 띄웠으면) Chrome 을 종료한다.
    with 블록에는 fetch_html(query, page) 를 주는 헬퍼를 yield 한다. 잠금을 못 잡으면 None."""
    # 상시 Chrome이 이미 떠 있으면(com.cpk.chrome) 잠금 없이 탭만 연다 — 여러 검색이 동시에 가능.
    # Chrome을 새로 띄워야 할 때만 browser_lock으로 직렬화한다(중복 실행·프로필 경쟁 방지).
    if chrome_alive():
        lock_cm = contextlib.nullcontext(True)
        need_launch = False
    else:
        lock_cm = cs.browser_lock()
        need_launch = True
    with lock_cm as got:
        if not got:
            cs.log("browser: 다른 브라우저 작업이 진행 중 — 검색 세션 못 엶")
            yield None
            return
        started = False
        if need_launch and not chrome_alive():
            launch_chrome()
            started = True
            cs.log("browser: Chrome 시작(검색 세션)")
        tab = Tab()
        try:
            # 워밍업(홈 방문)도 실제 요청이므로 공통 관문을 통과한다(중단·예산·간격 적용).
            # 중단·예산이면 워밍업을 건너뛴다 — fetch 에서 다시 판정한다.
            # UI 모드는 fetch 가 매번 홈→검색창 제출을 하므로 세션 워밍업을 생략한다.
            if warmup and SEARCH_MODE != "ui" and not (FRESH_CONTEXT or PROXY):
                try:
                    with cs.request_gate("warmup"):
                        tab.goto(cs.HOME_URL)
                except (cs.RequestPaused, cs.BudgetExceeded):
                    pass
        except Exception:
            tab.close()
            if started:
                quit_chrome()
            raise

        def fetch(query: str, page: int = 1, tries: int = 3) -> dict:
            """검색 한 건. 결과를 구조화 dict로 반환한다(http_status·outcome·product_count·html…).
            CPK_SEARCH_MODE=ui 면 홈에서 검색창 입력·제출(사용자 흐름), 아니면 검색 URL 직접 이동.
            재시도마다 검색 게이트를 통과해 간격을 각각 적용한다."""
            url = cs.SEARCH_URL.format(q=urllib.parse.quote(query))
            if page > 1:
                url += f"&page={page}"
            last: dict | None = None
            # 프록시가 설정되면 검색마다 그 프록시로 라우팅되는 새 시크릿 컨텍스트를 쓴다(IP 분산+오염 방지).
            use_fresh = FRESH_CONTEXT or bool(PROXY)
            for i in range(tries):
                if use_fresh:
                    t, close_ctx = open_incognito_tab(PROXY)
                else:
                    t, close_ctx = tab, (lambda: None)
                try:
                    try:
                        if SEARCH_MODE == "ui" or use_fresh:
                            # ui 흐름 또는 cold 컨텍스트는 홈을 먼저 방문(워밍업).
                            with cs.request_gate("warmup"):
                                t.goto(cs.HOME_URL)
                            # cold 컨텍스트(시크릿/프록시)는 검색 전 _abck 센서를 데운다(soft 챌린지 회피).
                            if use_fresh and WARM_SECS > 0:
                                warm_context(t)
                            with cs.request_gate(kind):
                                if SEARCH_MODE == "ui":
                                    t.submit_search(query, settle=(3.0, 5.0) if i == 0 else (5.0, 8.0))
                                else:
                                    t.goto(url, settle=(3.0, 5.0) if i == 0 else (5.0, 8.0))
                        else:
                            with cs.request_gate(kind):
                                t.goto(url, settle=(3.0, 5.0) if i == 0 else (5.0, 8.0))
                    except (cs.RequestPaused, cs.BudgetExceeded) as e:
                        if last is not None and last["outcome"] != "ok":
                            break
                        gate = "paused" if isinstance(e, cs.RequestPaused) else "budget"
                        return {"http_status": None, "outcome": gate, "product_count": 0,
                                "html": "", "final_url": url, "title": "", "error": None,
                                "remaining": getattr(e, "remaining", 0)}
                    units = t.eval(PRODUCT_JS) or 0
                    html = t.eval("document.documentElement.outerHTML") or ""
                    title = t.eval("document.title") or ""
                    final_url = t.eval("location.href") or url
                    outcome = classify_outcome(t.last_status, html, units)
                    last = {"http_status": t.last_status, "outcome": outcome, "product_count": units,
                            "html": html, "final_url": final_url, "title": title, "error": t.last_error}
                finally:
                    close_ctx()   # 시크릿 컨텍스트 정리(공유 탭이면 no-op)
                if outcome != "ok":
                    _save_evidence(query, page, last)  # 증거는 매 시도 보존
                # 정상이거나 정상 무결과면 재시도 무의미. challenge/오류만 리로드 재시도.
                if outcome in ("ok", "no_results"):
                    break
            if last is None:
                last = {"http_status": None, "outcome": "load_error", "product_count": 0,
                        "html": "", "final_url": url, "title": "", "error": None}
            # 재시도를 다 한 뒤에도 명확한 차단이면 공통 중단(리로드로 풀릴 챌린지를 미리 막지 않음).
            if last["outcome"] not in ("ok", "no_results"):
                cs.maybe_pause_on_block(last["outcome"], last["http_status"], reason=f"search {kind}")
            return last

        try:
            yield fetch
        finally:
            try:
                tab.close()
            finally:
                if started:
                    quit_chrome()


def search(query: str, page: int = 1, kind: str = "ondemand", with_autocomplete: bool = True) -> dict:
    """브라우저로 검색 한 건을 파싱해 반환한다. outcome으로 결과·무결과·차단·오류를 구분한다.
    연관검색어는 검색 페이지(브라우저)에서, 자동완성은 공용 헬퍼(cs.autocomplete_keywords)에서 온다."""
    import cpk_search as sr
    with search_session(kind) as fetch:
        if fetch is None:
            return {"query": query, "page": page, "outcome": "busy", "http_status": None,
                    "count": 0, "badge_counts": {}, "ads": 0, "related_keywords": [],
                    "autocomplete": [], "autocomplete_query": query, "autocomplete_ok": None,
                    "items": [], "total_count": None}
        r = fetch(query, page)
    html, outcome = r["html"], r["outcome"]
    rows = sr.parse(html) if outcome == "ok" else []
    counts: dict = {}
    for row in rows:
        counts[row["badge"]] = counts.get(row["badge"], 0) + 1
    # 자동완성은 검색 페이지가 막혀도(관대한 API) 시도한다. 단 중단·예산이면 새 요청을 보내지 않는다.
    if with_autocomplete and outcome not in ("paused", "budget"):
        ac = cs.autocomplete_keywords(query)
    else:
        ac = {"ok": None, "items": [], "used": query}
    return {"query": query, "page": page, "outcome": outcome, "http_status": r["http_status"],
            "count": len(rows), "total_count": sr.parse_total_count(html) if outcome == "ok" else None,
            "badge_counts": counts, "ads": sum(row["ad"] for row in rows),
            "related_keywords": sr.parse_related_keywords(html) if outcome == "ok" else [],
            "autocomplete": ac["items"], "autocomplete_query": ac["used"], "autocomplete_ok": ac["ok"],
            "items": rows}


def save(res: dict) -> int:
    header = "; ".join(f"{c['name']}={c['value']}" for c in res["cookies"])
    n = cs.import_cookie_header(header)
    cs.reset_ua_override()
    UA_FILE.write_text(json.dumps({"user-agent": res["ua"],
                                   "sec-ch-ua-platform": '"macOS"'}, ensure_ascii=False),
                       encoding="utf-8")
    return n


def available() -> bool:
    return os.path.exists(CHROME)


def reissue(query: str = "계란보관함", verify: bool = True, keep_chrome: bool = False,
            lock_timeout: float = 2700, hold_lock: bool = False) -> bool:
    """Chrome으로 새 세션을 받아 쿠키통을 교체한다. 성공(검증 통과) 여부 반환.
    쿠키통 저장은 jar_lock 안에서 하므로 collect가 도는 중이면 끝날 때까지 기다린다(최대 lock_timeout)."""
    if not available():
        cs.log(f"browser: Chrome 없음 ({CHROME})")
        return False
    # 재발급은 한 번에 하나만. 다른 잡이 이미 Chrome을 조작 중이면 중복 실행하지 않는다.
    with cs.browser_lock() as got:
        if not got:
            cs.log("browser: 다른 재발급이 진행 중 — 건너뜀")
            return False
        return _reissue_locked(query, verify, keep_chrome, lock_timeout, hold_lock)


def _reissue_locked(query, verify, keep_chrome, lock_timeout, hold_lock) -> bool:
    started = False
    try:
        if not chrome_alive():
            launch_chrome()
            started = True
            cs.log("browser: Chrome 시작")
        # Chrome을 켠 채로 최대 2회 시도한다(프로필이 식어 첫 회에 센서를 못 넘길 때 대비).
        for attempt in range(1, 3):
            res = harvest(query)
            akamai = [c["name"] for c in res["cookies"] if c["name"] in cs.AKAMAI]
            cs.log(f"browser: attempt={attempt} home={res['title_home']!r} "
                   f"search_units={res['browser_units']} cookies={len(res['cookies'])} akamai={akamai}")
            if not res["cookies"] or "_abck" not in akamai:
                cs.log("browser: 아카마이 쿠키가 없음")
                time.sleep(random.uniform(4, 8))
                continue
            lock = contextlib.nullcontext() if hold_lock else cs.jar_lock(timeout=lock_timeout)
            with lock:
                n = save(res)
                cs.log(f"browser: 쿠키 {n}개 저장, UA={res['ua'][:60]}")
                # 검증은 harvest 가 이미 브라우저로 한 검색 결과(browser_units)로 판정한다.
                # 별도 파이썬 canary(관문 밖 HTTP)를 보내지 않는다.
                if not verify or res["browser_units"] > 0:
                    # harvest 뒤 중단이 걸렸다면 재발급 성공으로 보지 않고 상위에 알린다.
                    if cs.paused_remaining() > 0:
                        cs.log("browser: 재발급 직후 중단 상태 — 성공으로 처리하지 않음")
                        return False
                    return True
            time.sleep(random.uniform(5, 10))
        return False
    except Exception as e:
        cs.log(f"browser: 오류 {e!r}")
        return False
    finally:
        if started and not keep_chrome:
            quit_chrome()


def main() -> int:
    import json as _json
    # 첫 인자가 'search' 면 온디맨드 브라우저 검색, 아니면 기존 재발급 동작.
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        ap = argparse.ArgumentParser(prog="cpk_browser.py search")
        ap.add_argument("query")
        ap.add_argument("--page", type=int, default=1)
        ap.add_argument("--json", action="store_true")
        ap.add_argument("--top", type=int, default=20)
        a = ap.parse_args(sys.argv[2:])
        res = search(a.query, a.page)
        if a.json:
            print(_json.dumps(res, ensure_ascii=False, indent=1))
        else:
            tc = f" total={res['total_count']:,}" if res.get("total_count") else ""
            print(f"[{res['query']}] outcome={res['outcome']} http={res['http_status']} "
                  f"count={res['count']}{tc} badges={res['badge_counts']} ads={res['ads']}")
            if res["related_keywords"]:
                print("연관검색어: " + " | ".join(res["related_keywords"]))
            if res.get("autocomplete"):
                print("자동완성: " + " | ".join(res["autocomplete"]))
            for r in res["items"][: a.top]:
                fee = "무료" if r["fee"] == 0 else (f"{r['fee']:,}" if r["fee"] else "-")
                print(f"{r['badge']:8s} | {r['price'] or 0:>7,} | 배송 {fee:>5s} | "
                      f"리뷰 {r['reviews']:>5,} | {'AD' if r['ad'] else '  '} | {r['name'][:50]}")
        return 0 if res["outcome"] == "ok" else 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="계란보관함")
    ap.add_argument("--keep-chrome", action="store_true", help="끝나도 Chrome을 종료하지 않음")
    ap.add_argument("--verify", action="store_true", help="저장 후 파이썬 세션으로 검색 검증")
    ap.add_argument("--jitter", type=int, default=0, help="시작 전 0~N초 무작위 대기(주기 분산용)")
    a = ap.parse_args()
    if a.jitter > 0:
        time.sleep(random.uniform(0, a.jitter))
    return 0 if reissue(a.query, verify=a.verify, keep_chrome=a.keep_chrome) else 1


if __name__ == "__main__":
    sys.exit(main())
