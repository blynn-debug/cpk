"""통합 전수 테스트 — 맥미니에서 전체 경로를 실제로 돌린다.
운영과 격리한다: 쿠키통·상태·데이터는 임시 폴더, 브라우저는 **전용 포트(9333)·전용 프로필**을 쓴다.
운영 Chrome(com.cpk.chrome, 포트 9222, 운영 프로필)은 시작하지도 종료하지도 않는다.
전용 프로필은 처음이라 아카마이 센서를 못 넘을 수 있어, 프로브가 검색 0이면 전체 skip 한다.
실행: cd ~/cpk && set -a && . ./cpk.env && set +a && .venv/bin/python -m unittest tests.test_integration
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import cpk_session as cs
import cpk_browser as cb

TEST_PORT = 9333  # 운영(9222)과 분리된 전용 CDP 포트


def _fresh_home():
    d = Path(tempfile.mkdtemp(prefix="cpk_it_"))
    cs.HOME = d
    cs.JAR = d / "jar.json"
    cs.HEALTH = d / "health.json"
    cs.LOG = d / "keepalive.log"
    cb.UA_FILE = d / "ua.json"
    cs.reset_ua_override()
    return d


@unittest.skipUnless(sys.platform == "darwin", "통합 테스트는 맥미니(darwin)에서만")
@unittest.skipUnless(cb.available(), "Chrome 없음")
class FullPath(unittest.TestCase):
    """브라우저 재발급 → keepalive → collect 전 구간을 전용 포트·프로필에서 실행(운영 미간섭)."""

    @classmethod
    def setUpClass(cls):
        # 전용 포트·프로필로 격리 — 운영 Chrome(9222)을 건드리지 않는다.
        cb.PORT = TEST_PORT
        cb.BASE = f"http://127.0.0.1:{TEST_PORT}"
        cls.profile = Path(tempfile.mkdtemp(prefix="cpk_it_profile_"))
        cb.PROFILE = cls.profile
        cls._homes = []
        if not cb.chrome_alive():
            cb.launch_chrome()
        try:
            probe = cb.harvest("계란보관함")
        except Exception as e:
            cb.quit_chrome()
            raise unittest.SkipTest(f"브라우저 프로브 실패: {e!r}")
        if not probe.get("browser_units"):
            cb.quit_chrome()
            raise unittest.SkipTest("전용 프로필이 아카마이 센서를 못 넘음(검색 0) — 통합 skip")

    @classmethod
    def tearDownClass(cls):
        cb.quit_chrome()  # 전용 프로필 패턴으로만 종료 — 운영 Chrome 무사
        shutil.rmtree(cls.profile, ignore_errors=True)
        for h in cls._homes:
            shutil.rmtree(h, ignore_errors=True)

    def setUp(self):
        self.home = _fresh_home()
        type(self)._homes.append(self.home)

    def test_1_browser_reissue_creates_working_session(self):
        ok = cb.reissue(query="계란보관함", verify=True, keep_chrome=True)
        self.assertTrue(ok, "브라우저 재발급/검증 실패")
        self.assertTrue(cs.JAR.exists())
        self.assertIn("Macintosh", cs.ua_override().get("user-agent", ""))
        akamai = {c["name"] for c in cs.load_jar()} & set(cs.AKAMAI)
        self.assertIn("_abck", akamai)

    def test_2_keepalive_ok_after_reissue(self):
        self.assertTrue(cb.reissue(verify=True, keep_chrome=True))
        import os
        import cpk_keepalive as ka
        os.environ["CPK_CANARY_EVERY"] = "1"
        rc = ka.main()
        self.assertEqual(rc, 0)
        self.assertTrue(cs.read_health()["ok"])

    def test_3_keepalive_self_heals_broken_jar(self):
        self.assertTrue(cb.reissue(verify=True, keep_chrome=True))
        jar = json.loads(cs.JAR.read_text(encoding="utf-8"))
        for c in jar:
            if c["name"] in cs.AKAMAI:
                c["value"] = "BROKEN"
        cs.JAR.write_text(json.dumps(jar), encoding="utf-8")
        cs.write_health({"ok": False, "runs": 1, "fails": 1})
        import os
        import cpk_keepalive as ka
        os.environ["CPK_CANARY_EVERY"] = "1"
        rc = ka.main()  # 2회째 → 자동 재발급으로 복구
        self.assertEqual(rc, 0, "자동 재발급으로 복구되지 않음")
        h = cs.read_health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["fails"], 0)

    def test_4_collect_writes_files_and_summary(self):
        self.assertTrue(cb.reissue(verify=True, keep_chrome=True))
        import cpk_collect as cc
        data = self.home / "data"
        cc.DATA = data
        cc.STATE = self.home / "collect.json"
        kwf = self.home / "keywords.txt"
        kwf.write_text("방충망 잠금장치\n", encoding="utf-8")
        cc.KEYWORDS = kwf
        cc.QUIET = ""
        cc.SYNC_TARGET = ""
        cc.GAP_MIN = cc.GAP_MAX = 0.0
        rc = cc.run()
        self.assertEqual(rc, 0)
        latest = list((data / "latest").glob("*.json"))
        self.assertEqual(len(latest), 1)
        res = json.loads(latest[0].read_text(encoding="utf-8"))
        self.assertEqual(res["count"], 60)
        self.assertTrue((data / "runs.jsonl").exists())
        summ = json.loads((data / "runs.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(summ["outcome"], "ok")
        self.assertEqual(len(summ["top"]), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
