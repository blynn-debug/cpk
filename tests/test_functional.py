"""기능 테스트 — 실제 쿠팡 응답으로 핵심 기능을 확인한다(맥미니에서 실행).
현재 운영 경로(실제 Chrome·브라우저 검색)를 기준으로 판정한다. 폐기된 파이썬 경로는 쓰지 않는다.
실행: cd ~/cpk && set -a && . ./cpk.env && set +a && CPK_HOME=~/cpk/state .venv/bin/python -m unittest tests.test_functional

주의: 이 테스트는 CPK_HOME=운영 state 로 돌리면 운영 control.json(중단·예산·간격)을 공유한다.
실제 쿠팡 요청을 소비하므로 저빈도로만 실행한다.
"""
import unittest

import cpk_browser as cb
import cpk_session as cs


def _session_alive() -> bool:
    """지금 브라우저로 검색이 되는지 프로브. 차단 구간이면 기능 테스트를 건너뛴다(코드 문제와 구분)."""
    try:
        return cb.available() and cb.search("계란보관함", kind="health")["outcome"] in ("ok", "no_results")
    except Exception:
        return False


@unittest.skipUnless(cb.available(), "Chrome 없음 — 기능 테스트 생략")
@unittest.skipUnless(_session_alive(), "지금 브라우저 검색이 안 되는 구간 — 기능 테스트 생략(코드 문제 아님)")
class Live(unittest.TestCase):
    def test_search_full_shape(self):
        res = cb.search("방충망 잠금장치")
        self.assertEqual(res["outcome"], "ok")
        self.assertEqual(res["count"], 60)
        self.assertGreater(res["ads"], 0)
        self.assertTrue(all(r["price"] and r["product_id"] for r in res["items"]))
        self.assertTrue(res["related_keywords"], "연관검색어 비어 있음")
        self.assertTrue(res["autocomplete"], "자동완성 비어 있음")

    def test_autocomplete_alive(self):
        r = cs.autocomplete_keywords("2단 선반")
        self.assertTrue(r["ok"])
        self.assertTrue(r["items"])

    def test_uses_mac_ua_when_override_present(self):
        ov = cs.ua_override()
        if not ov:
            self.skipTest("ua.json 없음(브라우저 재발급 전)")
        self.assertIn("Macintosh", ov.get("user-agent", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
