"""cpk-web: 키워드 1건 온디맨드 검색 웹(Railway).
화면(입력+버튼) + /api/search(맥미니 SSH 중계). 크롤링은 맥미니에서만 실행된다.
"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

import miniclient

app = Flask(__name__)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()


def _password_ok(req) -> bool:
    if not APP_PASSWORD:
        return True
    sent = (req.headers.get("X-App-Password")
            or (req.get_json(silent=True) or {}).get("password", "")
            or req.form.get("password", ""))
    return sent == APP_PASSWORD


@app.get("/")
def index():
    return render_template("index.html", password_required=bool(APP_PASSWORD))


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


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
