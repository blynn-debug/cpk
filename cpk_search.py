"""쿠팡 검색 결과 첫 페이지 파싱.

사용: python cpk_search.py "검색어" [--json] [--page N]
출력: 배지(로켓/판매자로켓/일반), 가격, 배송비, 리뷰 수, 광고 여부, 상품명, 상품번호
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from bs4 import BeautifulSoup

import cpk_session as cs


def badge_of(unit) -> str:
    srcs = " ".join((i.get("src") or "") + " " + (i.get("alt") or "") for i in unit.find_all("img")).lower()
    text = unit.get_text(" ", strip=True)
    if "merchant" in srcs:
        return "판매자로켓"
    if "wow" in srcs:
        return "로켓와우"
    if "rocket" in srcs or "jikgu" in srcs:
        return "로켓"
    if "도착 보장" in text:
        return "로켓(도착보장)"
    return "일반"


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for u in soup.select('[class*="ProductUnit_productUnit"]'):
        t = re.sub(r"\s+", " ", u.get_text(" ", strip=True))
        name_el = u.select_one('[class*="productNameV2"]')
        name = name_el.get_text(" ", strip=True) if name_el else t[:60]
        a = u.find("a", href=True)
        href = a["href"] if a else ""
        pid = re.search(r"/vp/products/(\d+)", href)
        m = re.search(r"([\d,]+)원 \( 1개당", t) or re.search(r"(?<!최대 )([\d,]+)원", t.replace("할인", ""))
        fee = re.search(r"배송비 ([\d,]+)원", t)
        rv = re.search(r"\( ([\d,]+) \)", t)
        rows.append({
            "badge": badge_of(u),
            "price": int(m.group(1).replace(",", "")) if m else None,
            "fee": int(fee.group(1).replace(",", "")) if fee else (0 if "무료배송" in t else None),
            "reviews": int(rv.group(1).replace(",", "")) if rv else 0,
            "ad": t.endswith("광고"),
            "name": name,
            "product_id": pid.group(1) if pid else None,
            "url": ("https://www.coupang.com" + href.split("&q=")[0]) if href else None,
        })
    return rows


def parse_related_keywords(html: str) -> list[str]:
    """결과 페이지의 '연관검색어:' 링크. 본문 블록을 우선, 없으면 상단 고정바 블록."""
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one('[class*="srp_relatedKeywords"]') or soup.select_one(".srp-related-keywords")
    if not block:
        return []
    seen, out = set(), []
    for a in block.find_all("a"):
        kw = (a.get("title") or a.get_text(strip=True) or "").strip()
        if kw and kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


def parse_total_count(html: str) -> int | None:
    m = re.search(r'\\"searchCount\\":(\d+)', html) or re.search(r'"searchCount":(\d+)', html)
    return int(m.group(1)) if m else None


def main() -> int:
    # 구형 HTTP 검색 CLI는 폐기했다. 공통 요청 제어를 지나지 않는 관문 밖 경로였다.
    # 브라우저 경로를 쓴다: python cpk_browser.py search "검색어"
    print("cpk_search CLI는 폐기되었습니다. 다음을 쓰세요:\n"
          "  python cpk_browser.py search \"검색어\"\n"
          "  python cpk_keywords.py \"검색어\"   # 연관검색어/자동완성",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
