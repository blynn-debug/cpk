"""Railway → 맥미니 크롤러 SSH 중계.

전용 SSH 키(환경변수 SSH_KEY)로 aws103(ProxyJump)을 거쳐 맥미니의 forced-command 를 호출하고,
검색 1건의 JSON 을 받아 dict 로 돌려준다. 크롤링 자체는 맥미니에서만 돈다.

환경변수:
  SSH_KEY           전용 개인키(PEM 텍스트). 없으면 SSH_KEY_FILE 경로 사용.
  AWS103_HOST       aws103 공인 IP/호스트
  AWS103_USER       aws103 사용자(기본 ec2-user)
  MINI_USER         맥미니 사용자(기본 mini_worker)
  MINI_TUNNEL_PORT  aws103 에서 맥미니로 가는 역터널 포트(기본 2222)
  SSH_TIMEOUT       ssh 전체 타임아웃 초(기본 170)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid

KEYWORD_RE = re.compile(r"^[\w가-힣ㄱ-ㅎㅏ-ㅣ0-9 ().,+&/-]{1,60}$")

_HUMAN = {
    "ok": None,
    "no_results": "결과가 없습니다.",
    "challenge": "지금 차단 구간이라 실패했어요. 잠시 후 다시 시도해 주세요.",
    "http_error": "쿠팡이 일시적으로 막았어요. 잠시 후 다시 시도해 주세요.",
    "load_error": "페이지를 불러오지 못했어요. 다시 시도해 주세요.",
    "paused": "요청 제어로 잠시 대기 중이에요(차단 백오프). 나중에 다시 시도해 주세요.",
    "budget": "오늘 조회 한도에 도달했어요. 내일 다시 시도해 주세요.",
    "busy": "다른 검색이 진행 중이에요. 잠시 후 다시 시도해 주세요.",
    "error": "크롤러 오류가 발생했어요.",
    "timeout": "시간이 초과됐어요(최대 대기). 다시 시도해 주세요.",
    "transport": "맥미니에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.",
    "badinput": "검색어에 허용되지 않는 문자가 있어요.",
}


def human_message(outcome: str) -> str | None:
    return _HUMAN.get(outcome, "알 수 없는 오류가 발생했어요.")


def valid_keyword(q: str) -> bool:
    s = (q or "").strip()
    return bool(s) and bool(KEYWORD_RE.match(s))


def _key_path() -> str:
    """SSH_KEY(내용) 또는 SSH_KEY_FILE(경로)에서 개인키 파일 경로를 확보한다."""
    path = os.environ.get("SSH_KEY_FILE", "").strip()
    if path and os.path.exists(path):
        return path
    key = os.environ.get("SSH_KEY", "")
    if not key.strip():
        raise RuntimeError("SSH_KEY(또는 SSH_KEY_FILE) 미설정")
    if not key.endswith("\n"):
        key += "\n"
    fd, p = tempfile.mkstemp(prefix="cpk_key_", suffix=".pem")
    with os.fdopen(fd, "w") as f:
        f.write(key)
    os.chmod(p, 0o600)
    return p


def build_ssh_command(keyword: str, key_path: str) -> list[str]:
    """맥미니 forced-command 를 호출하는 ssh argv 를 만든다. keyword 는 원격 명령(SSH_ORIGINAL_COMMAND)."""
    host = os.environ.get("AWS103_HOST", "").strip()
    if not host:
        raise RuntimeError("AWS103_HOST 미설정")
    aws_user = os.environ.get("AWS103_USER", "ec2-user").strip()
    aws_port = os.environ.get("AWS103_PORT", "22").strip()  # 점프 호스트 SSH 포트(대체 포트 대비)
    mini_user = os.environ.get("MINI_USER", "mini_worker").strip()
    port = os.environ.get("MINI_TUNNEL_PORT", "2222").strip()
    common = [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=6",
    ]
    # ProxyJump 은 점프 호스트에 -i 키를 넘기지 않는다(컨테이너엔 기본 키가 없어 점프 인증 실패).
    # → ProxyCommand 로 점프에도 같은 전용 키를 명시한다.
    proxy_cmd = " ".join([
        "ssh", "-i", key_path, "-W", "%h:%p", "-p", aws_port,
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=15",
        f"{aws_user}@{host}",
    ])
    return [
        "ssh", "-i", key_path,
        "-o", "ProxyCommand=" + proxy_cmd,
        *common,
        "-p", port,
        f"{mini_user}@127.0.0.1",
        keyword,  # forced-command 가 SSH_ORIGINAL_COMMAND 로 받는다
    ]


def parse_output(stdout: str) -> dict:
    """맥미니가 낸 JSON 한 줄을 파싱한다. 마지막 JSON 라인을 채택(잡음 대비)."""
    for line in reversed([ln for ln in stdout.splitlines() if ln.strip()]):
        try:
            return json.loads(line)
        except Exception:
            continue
    return {"outcome": "error", "error": "맥미니 응답을 파싱하지 못함", "items": []}


MARKET_SENTINEL = "__market_report__"


def market_report() -> dict:
    """맥미니 리포트를 SSH로 가져온다. 실패는 {'markets':[], 'error':..} 로."""
    try:
        key_path = _key_path()
    except Exception as e:
        return {"markets": [], "error": str(e), "message": human_message("error")}
    cmd = build_ssh_command(MARKET_SENTINEL, key_path)
    timeout = float(os.environ.get("SSH_TIMEOUT", "170"))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"markets": [], "error": "timeout", "message": human_message("timeout")}
    if p.returncode != 0 and not p.stdout.strip():
        return {"markets": [], "error": (p.stderr or "").strip()[-300:],
                "message": human_message("transport")}
    res = parse_output(p.stdout)
    res.setdefault("markets", [])
    return res


COLLECT_PREFIX = "__collect__ "


def worker_request(payload: dict) -> dict:
    """Start/read an on-demand job; SSH only waits for a short local socket response."""
    started = time.monotonic()
    outcome = "transport"
    temporary_key = None
    try:
        key_path = _key_path()
        if key_path != os.environ.get("SSH_KEY_FILE", "").strip():
            temporary_key = key_path
        encoded = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
        command = build_ssh_command("__worker__ " + encoded, key_path)
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
        if process.returncode and not process.stdout.strip():
            return {"error": "transport", "message": human_message("transport")}
        result = parse_output(process.stdout)
        if result.get("outcome") == "error" and "request_id" not in result:
            return {"error": "worker_unavailable", "message": "검색 작업 서버에 연결하지 못했어요."}
        result["transport_ms"] = round((time.monotonic() - started) * 1000, 1)
        outcome = result.get("error") or result.get("state", "ok")
        return result
    except subprocess.TimeoutExpired:
        outcome = "timeout"
        return {"error": "timeout", "message": human_message("timeout")}
    except Exception:
        return {"error": "transport", "message": human_message("transport")}
    finally:
        logging.getLogger("cpk.timing").warning(json.dumps({
            "request_id": payload.get("request_id"), "stage": "ssh", "op": payload.get("op"),
            "duration_ms": round((time.monotonic() - started) * 1000, 1), "outcome": outcome,
        }))
        if temporary_key:
            try:
                os.unlink(temporary_key)
            except OSError:
                pass


def start_job(keyword: str, request_id: str | None = None) -> dict:
    """Submit a validated keyword with an idempotency key."""
    if not valid_keyword(keyword):
        return {"error": "badinput"}
    try:
        request_id = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError):
        return {"error": "badinput"}
    return worker_request({"op": "start", "query": keyword.strip(), "request_id": request_id})


def get_job(request_id: str) -> dict:
    """Read a job without starting another collection."""
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        return {"error": "badinput"}
    return worker_request({"op": "get", "request_id": request_id})


def collect(keyword: str) -> dict:
    """검색창(온디맨드): 키워드 1건을 맥미니에서 '수집'(검색→저장→점수→소싱)한 뒤
    갱신된 마켓 리포트를 돌려준다. 실패는 {'markets':[], 'error':..} 로."""
    keyword = (keyword or "").strip()
    if not valid_keyword(keyword):
        return {"markets": [], "error": "badinput", "message": human_message("badinput")}
    try:
        key_path = _key_path()
    except Exception as e:
        return {"markets": [], "error": str(e), "message": human_message("error")}
    cmd = build_ssh_command(COLLECT_PREFIX + keyword, key_path)
    timeout = float(os.environ.get("SSH_COLLECT_TIMEOUT", os.environ.get("SSH_TIMEOUT", "175")))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"markets": [], "error": "timeout", "message": human_message("timeout")}
    if p.returncode != 0 and not p.stdout.strip():
        return {"markets": [], "error": (p.stderr or "").strip()[-300:],
                "message": human_message("transport")}
    res = parse_output(p.stdout)
    res.setdefault("markets", [])
    return res


def search(keyword: str) -> dict:
    """키워드 1건을 맥미니에서 검색해 결과 dict 를 돌려준다. 실패도 outcome 으로 표현."""
    keyword = (keyword or "").strip()
    if not valid_keyword(keyword):
        return {"query": keyword, "outcome": "badinput", "items": [],
                "message": human_message("badinput")}
    try:
        key_path = _key_path()
    except Exception as e:
        return {"query": keyword, "outcome": "error", "items": [], "error": str(e),
                "message": human_message("error")}
    cmd = build_ssh_command(keyword, key_path)
    timeout = float(os.environ.get("SSH_TIMEOUT", "170"))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"query": keyword, "outcome": "timeout", "items": [],
                "message": human_message("timeout")}
    if p.returncode != 0 and not p.stdout.strip():
        return {"query": keyword, "outcome": "transport", "items": [],
                "error": (p.stderr or "").strip()[-300:], "message": human_message("transport")}
    res = parse_output(p.stdout)
    res.setdefault("query", keyword)
    res["message"] = human_message(res.get("outcome", "error"))
    return res
