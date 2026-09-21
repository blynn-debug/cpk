"""cpk-web: 키워드 1건 온디맨드 검색 웹(Railway).
화면(입력+버튼) + /api/search(맥미니 SSH 중계). 크롤링은 맥미니에서만 실행된다.
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid

import miniclient
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()


def _password_ok(req) -> bool:
    if not APP_PASSWORD:
        return True
    data = req.get_json(silent=True)
    sent = (req.headers.get("X-App-Password")
            or (data.get("password", "") if isinstance(data, dict) else "")
            or req.form.get("password", ""))
    return sent == APP_PASSWORD


@app.get("/")
def index():
    return render_template("index.html", password_required=bool(APP_PASSWORD))


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/api/diag")
def diag():
    """배포/네트워크 진단: 새 코드 버전 표식 + aws103:22 TCP 도달성 + 키 존재 여부."""
    import socket
    host = os.environ.get("AWS103_HOST", "").strip()
    port = int(os.environ.get("AWS103_PORT", "22"))
    res = {"version": "diag-3",
           "aws103_host_set": bool(host),
           "aws103_port": port,
           "has_ssh_key": bool(os.environ.get("SSH_KEY", "").strip())}

    def probe(h, p):
        try:
            socket.create_connection((h, p), timeout=8).close()
            return "ok"
        except Exception as e:
            return f"fail: {type(e).__name__}"

    res["tcp_aws103"] = probe(host, port)          # aws103 지정 포트
    res["tcp_github_22"] = probe("github.com", 22)  # 아웃바운드 22 자체가 되나(대조)
    res["tcp_github_443"] = probe("github.com", 443)  # 아웃바운드 일반(대조)
    return jsonify(res)


@app.get("/markets")
def markets_page():
    return render_template("markets.html", password_required=bool(APP_PASSWORD))


@app.get("/guide")
def guide_page():
    return render_template("guide.html")


@app.get("/api/markets")
def api_markets():
    return jsonify(miniclient.market_report())


@app.post("/api/market_collect")
def api_market_collect():
    """검색창(온디맨드): 키워드 1건을 수집한 뒤 갱신된 마켓 리포트를 돌려준다."""
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()
    if not _password_ok(request):
        return jsonify({"markets": [], "error": "auth", "message": "비밀번호가 필요하거나 틀렸어요."}), 401
    if not miniclient.valid_keyword(q):
        return jsonify({"markets": [], "error": "badinput",
                        "message": miniclient.human_message("badinput")}), 400
    return jsonify(miniclient.collect(q))


@app.post("/api/jobs")
def start_job():
    if not _password_ok(request):
        return jsonify({"error": "auth", "message": "비밀번호를 확인해 주세요."}), 401
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "badinput"}), 400
    query = data.get("q", "")
    if not isinstance(query, str) or not miniclient.valid_keyword(query):
        return jsonify({"error": "badinput"}), 400
    result = miniclient.start_job(query, data.get("request_id"))
    status = 202 if result.get("request_id") else {"badinput": 400, "request_id_conflict": 409}.get(result.get("error"), 503)
    return jsonify(result), status


@app.get("/api/jobs/<request_id>")
def get_job(request_id):
    if not _password_ok(request):
        return jsonify({"error": "auth", "message": "비밀번호를 확인해 주세요."}), 401
    result = miniclient.get_job(request_id)
    status = 200 if result.get("request_id") else {"badinput": 400, "not_found": 404}.get(result.get("error"), 503)
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@app.post("/api/jobs/<request_id>/timing")
def job_timing(request_id):
    """Correlate browser-visible latency with SSH/worker timings, excluding query and secrets."""
    if not _password_ok(request):
        return jsonify({"error": "auth"}), 401
    try:
        request_id = str(uuid.UUID(request_id))
    except ValueError:
        return jsonify({"error": "badinput"}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "badinput"}), 400
    record = {"request_id": request_id, "stage": "browser_display"}
    for name in ("first_result_ms", "essential_ready_ms", "elapsed_ms"):
        value = data.get(name)
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 180000:
            return jsonify({"error": "badinput"}), 400
        record[name] = round(value, 1)
    logging.getLogger("cpk.timing").warning(json.dumps(record))
    return "", 204


@app.post("/api/search")
def api_search():
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()
    if not _password_ok(request):
        return jsonify({"outcome": "auth", "message": "비밀번호가 필요하거나 틀렸어요.", "items": []}), 401
    if not miniclient.valid_keyword(q):
        return jsonify({"outcome": "badinput",
                        "message": miniclient.human_message("badinput"), "items": []}), 400
    result = miniclient.search(q)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
