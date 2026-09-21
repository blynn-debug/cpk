"""Playwright acceptance checks; API boundary is deterministic and makes no target requests."""

import copy
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    phase = {"value": 1, "starts": 0}
    job = {
        "request_id": "c076c224-68c4-4f01-9d08-e4899dd0ed16",
        "query": "시험 검색",
        "state": "running",
        "sections": {"search": "pending", "autocomplete": "ok", "sourcing": "pending"},
        "result": {},
        "autocomplete": {"ok": True, "items": ["자동완성 먼저"]},
        "sourcing": {},
    }

    def respond(route):
        path = urlsplit(route.request.url).path
        if path == "/api/markets":
            data = {"markets": []}
        else:
            if path == "/api/jobs":
                phase["starts"] += 1
            data = copy.deepcopy(job)
            if phase["value"] >= 2:
                data["sections"]["search"] = "ok"
                data["result"] = {
                    "outcome": "ok",
                    "query": "시험 검색",
                    "count": 1,
                    "related_keywords": ["연관검색어 먼저"],
                    "items": [{"name": "테스트 상품", "price": 3000, "reviews": 5, "badge": "일반"}],
                }
            if phase["value"] >= 3:
                data["state"] = "partial"
                data["sections"]["sourcing"] = "load_error"
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    with sync_playwright() as playwright:
        executable = os.environ.get("CPK_TEST_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        browser = playwright.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", respond)
        page.goto("http://127.0.0.1:18080/markets")
        page.wait_for_load_state("networkidle")
        page.get_by_label("수집할 키워드").fill("시험 검색")
        page.get_by_role("button", name="수집하기", exact=True).click()
        page.locator("#detail").get_by_text("자동완성 먼저", exact=True).wait_for()
        assert "조회 중" in page.locator("#detail").inner_text()
        assert "없음" not in page.locator("#detail").inner_text()
        phase["value"] = 2
        page.get_by_text("테스트 상품", exact=True).wait_for()
        assert "도매꾹 조회 중" in page.locator("#detail").inner_text()
        output = Path(os.environ.get("CPK_UI_ARTIFACTS", "ui-artifacts"))
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / "progress.png"), full_page=True)
        phase["value"] = 3
        page.get_by_role("button", name="수집하기", exact=True).wait_for(state="visible")
        page.wait_for_function("!document.querySelector('#collectBtn').disabled")
        assert "도매꾹 조회 실패" in page.locator("#detail").inner_text()
        assert "테스트 상품" in page.locator("#detail").inner_text()
        assert phase["starts"] == 1, "Polling must not submit another search"
        assert not errors, errors
        page.screenshot(path=str(output / "partial.png"), full_page=True)
        phase.update(value=1, starts=0)
        page.goto("http://127.0.0.1:18080/")
        page.locator("#q").fill("시험 검색")
        page.locator("#go").click()
        page.locator("#results").get_by_text("자동완성 먼저", exact=True).wait_for()
        phase["value"] = 2
        page.get_by_text("테스트 상품", exact=True).wait_for()
        phase["value"] = 3
        page.wait_for_function("!document.querySelector('#go').disabled")
        assert phase["starts"] == 1
        assert page.evaluate("window.cpkLastTiming.essential_ready_ms !== null")
        assert not errors, errors
        browser.close()
        print(
            "PASS: autocomplete first, products before sourcing, partial failure preserved, one submission, no JS errors"
        )


if __name__ == "__main__":
    main()
