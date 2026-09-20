"""도매꾹 오픈API 존재판별(국내배송+우수공급사). 근거: domegg/docs/EXISTENCE_CHECK.md."""
from __future__ import annotations
import os, re, json, urllib.parse, urllib.request

ENDPOINT = "https://www.domeggook.com/ssl/api/"

def api_key() -> str:
    k = os.environ.get("CPK_DOMEGGOOK_KEY", "").strip()
    if k:
        return k
    path = os.environ.get("CPK_DOMEGGOOK_KEY_FILE",
                          os.path.expanduser("~/side-job-ecommerce/domegg/api_key/api.txt"))
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError as e:
        raise RuntimeError(f"도매꾹 API Key 없음(CPK_DOMEGGOOK_KEY 또는 {path})") from e
    m = re.search(r"[0-9a-fA-F]{20,}", txt)
    return m.group(0) if m else txt.strip()

def build_url(keyword, key, market="dome", premium=True, domestic=True, sz=5) -> str:
    q = {"ver": "4.1", "mode": "getItemList", "aid": key, "market": market,
         "om": "json", "kw": keyword, "sz": str(sz)}
    if premium:
        q["sgd"] = "true"     # 우수공급사(우수판매자)
    if domestic:
        q["dfos"] = "false"   # 국내배송(해외직배송 제외)
    return ENDPOINT + "?" + urllib.parse.urlencode(q)

def parse_response(body: str) -> dict:
    d = json.loads(body)
    root = d.get("domeggook", {})
    count = int(root.get("header", {}).get("numberOfItems", 0) or 0)
    items = root.get("list", {}).get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    samples = [{"title": it.get("title"), "price": it.get("price"),
                "seller": it.get("nick") or it.get("id"), "url": it.get("url")}
               for it in items]
    return {"count": count, "samples": samples}

def check_existence(keyword, market="dome", premium=True, domestic=True, opener=None) -> dict:
    opener = opener or urllib.request.urlopen
    url = build_url(keyword, api_key(), market, premium, domestic, sz=5)
    with opener(url, timeout=20) as r:
        body = r.read().decode("utf-8")
    parsed = parse_response(body)
    return {"keyword": keyword, "market": market, "exists": parsed["count"] > 0,
            "count": parsed["count"], "samples": parsed["samples"]}
