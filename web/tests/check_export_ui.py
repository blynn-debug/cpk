"""Actual browser downloads and navigation; only remote search APIs are substituted."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright


def main():
    output = Path(os.environ.get("CPK_UI_ARTIFACTS", "ui-export-artifacts"))
    output.mkdir(exist_ok=True, parents=True)
    fixture = {
        "markets": [
            {
                "keyword": f"보관함 {i}",
                "top": [{"name": f"보관함 상품 {i}", "price": 1000 + i}],
                "related": [f"연관어 {i}"],
                "autocomplete": [],
                "autocomplete_ok": 1,
                "related_ok": 1,
                "collected_at": "2026-09-22",
                "dome_exists": False,
                "dome_count": 0,
            }
            for i in range(12)
        ]
    }
    state = {"starts": 0, "query": "", "phase": 0, "empty": False, "export_fail": False}

    def respond(route):
        path = urlsplit(route.request.url).path
        if path == "/api/export":
            if state["export_fail"]:
                route.fulfill(status=500, json={"message": "저장 재시도 테스트"})
            else:
                route.continue_()  # Real Flask, openpyxl, bytes and download.
            return
        if path.endswith("/timing"):
            route.fulfill(status=204)
            return
        if path == "/api/markets":
            route.fulfill(json={"markets": []} if state["empty"] else fixture)
            return
        if path == "/api/jobs":
            state["starts"] += 1
            state["query"] = route.request.post_data_json["q"]
        query = state["query"]
        result = {
            "outcome": "ok",
            "query": query,
            "count": 60,
            "related_keywords": [f"추천 {i}" for i in range(10)],
            "items": [{"name": f"{query} 상품 {i}", "price": i, "reviews": i, "badge": "일반"} for i in range(60)],
        }
        data = {
            "request_id": "c076c224-68c4-4f01-9d08-e4899dd0ed16",
            "query": query,
            "state": "running" if state["phase"] == 0 else "complete",
            "sections": {"search": "ok", "autocomplete": "ok", "sourcing": "pending" if state["phase"] == 0 else "ok"},
            "result": result,
            "autocomplete": {"items": ["자동완성 확인"]},
            "sourcing": {"exists": True, "count": 5},
        }
        route.fulfill(json=data)

    def download(page, selector, filename):
        with page.expect_download() as pending:
            page.locator(selector).click()
        target = output / filename
        pending.value.save_as(target)
        return load_workbook(target)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=os.environ.get(
                "CPK_TEST_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", respond)
        page.goto("http://127.0.0.1:18080/markets", wait_until="networkidle")
        expect(page.locator("#exportAll")).to_have_text("전체 저장 (12)")
        expect(page.locator("#exportSelected")).to_be_disabled()
        page.locator('#results [data-kw="보관함 11"]').click()
        expect(page.locator("#detail h2")).to_contain_text("보관함 11")
        assert not page.locator("#marketList").evaluate("el => el.open")
        assert page.locator("#detail").bounding_box()["y"] >= page.locator("#workspaceTools").bounding_box()["height"]
        page.locator("#detail [data-export-query]").check()
        book = download(page, "#exportSelected", "selected.xlsx")
        assert book["키워드 요약"].max_row == 2 and book["키워드 요약"]["A2"].value == "보관함 11"
        book.close()
        book = download(page, "#exportAll", "all.xlsx")
        assert book["키워드 요약"].max_row == 13
        book.close()
        page.locator("#showMarketList").click()
        page.locator('#results [data-kw="보관함 0"]').click()
        page.locator('#recentQueries [data-kw="보관함 11"]').click()
        expect(page.locator("#detail [data-export-query]")).to_be_checked()
        assert state["starts"] == 0, "Cached navigation and Excel saves must not start searches"

        page.locator("#q").fill("새 키워드")
        page.locator("#collectBtn").click()
        expect(page.locator("#detail tbody tr")).to_have_count(60)
        page.locator("#detail [data-export-query]").check()
        page.locator("#detail details").first.evaluate("el => el.open = true")
        page.locator(".product-scroll").evaluate("el => el.scrollTop = 180")
        state["phase"] = 1
        expect(page.locator("#collectBtn")).to_be_enabled()
        expect(page.locator("#detail [data-export-query]")).to_be_checked()
        assert page.locator("#detail details").first.evaluate("el => el.open")
        assert page.locator(".product-scroll").evaluate("el => el.scrollTop") == 180
        page.locator('#recentQueries [data-kw="보관함 11"]').click()
        page.locator('#recentQueries [data-kw="새 키워드"]').click()
        expect(page.locator("#detail tbody tr")).to_have_count(60)
        assert state["starts"] == 1
        book = download(page, "#exportSelected", "two-selected.xlsx")
        assert book["키워드 요약"].max_row == 3 and book["상품"].max_row == 62
        assert book["상품"]["D3"].value == 0
        book.close()
        page.screenshot(path=str(output / "desktop.png"), full_page=True)

        state["export_fail"] = True
        page.locator("#exportSelected").click()
        expect(page.locator("#exportStatus")).to_have_text("저장 재시도 테스트")
        expect(page.locator("#exportSelected")).to_be_enabled()
        state["export_fail"] = False
        book = download(page, "#exportSelected", "retry.xlsx")
        book.close()

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('#recentQueries [data-kw="보관함 11"]').click()
        page.locator('#recentQueries [data-kw="새 키워드"]').click()
        assert page.locator("#workspaceTools").bounding_box()["y"] >= 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("#detail").bounding_box()["y"] < 450
        page.screenshot(path=str(output / "mobile.png"), full_page=True)

        state["phase"] = 0
        page.locator("#q").fill("늦은 결과")
        page.locator("#collectBtn").click()
        expect(page.locator("#detail h2")).to_contain_text("늦은 결과")
        page.locator('#recentQueries [data-kw="보관함 11"]').click()
        state["phase"] = 1
        page.wait_for_timeout(1500)
        expect(page.locator("#detail h2")).to_contain_text("보관함 11")
        assert page.locator("#detail").bounding_box()["y"] < 450

        state["empty"] = True
        page.goto("http://127.0.0.1:18080/markets", wait_until="networkidle")
        expect(page.locator("#exportAll")).to_be_disabled()
        expect(page.locator("#exportSelected")).to_be_disabled()
        page.goto("http://127.0.0.1:18080/", wait_until="networkidle")
        page.locator("#q").fill("루트 검색")
        page.locator("#go").click()
        expect(page.locator("#exportAll")).to_have_text("전체 저장 (1)")
        book = download(page, "#exportAll", "root.xlsx")
        assert book["상품"].max_row == 61
        book.close()
        assert not errors, errors
        browser.close()
    print(
        "PASS: real XLSX all/selection/retry, keyword navigation without searches, progress state, desktop/mobile, root"
    )


if __name__ == "__main__":
    main()
