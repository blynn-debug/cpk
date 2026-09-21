"""cpk-web 오프라인 유닛 테스트. 네트워크·SSH·크롤러 없음(전부 목).
실행: cd web && python -m unittest tests.test_web
cpk 관례(오프라인·결정적)와 동일하게, 실제 쿠팡/맥미니 호출 없이 로직만 검증한다.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import miniclient  # noqa: E402


class ValidKeyword(unittest.TestCase):
    def test_accepts_korean_and_space(self):
        self.assertTrue(miniclient.valid_keyword("계란보관함"))
        self.assertTrue(miniclient.valid_keyword("욕실 수납장"))
        self.assertTrue(miniclient.valid_keyword("무타공 선반 3단"))

    def test_rejects_empty_and_long_and_meta(self):
        self.assertFalse(miniclient.valid_keyword(""))
        self.assertFalse(miniclient.valid_keyword("   "))
        self.assertFalse(miniclient.valid_keyword("가" * 61))
        for bad in ["a; rm -rf /", "$(reboot)", "a`b`", "a|b", "a>b", 'a"b', "a'b", "a\nb"]:
            self.assertFalse(miniclient.valid_keyword(bad), bad)


class BuildSshCommand(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ.update({"AWS103_HOST": "1.2.3.4", "AWS103_USER": "ec2-user",
                           "MINI_USER": "mini_worker", "MINI_TUNNEL_PORT": "2222"})

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)

    def test_command_shape(self):
        cmd = miniclient.build_ssh_command("계란보관함", "/tmp/k.pem")
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("-i", cmd); self.assertIn("/tmp/k.pem", cmd)
        # 점프에도 전용 키를 넘기는 ProxyCommand(ProxyJump 은 -i 를 점프에 안 넘김)
        pc = [c for c in cmd if c.startswith("ProxyCommand=")]
        self.assertEqual(len(pc), 1, "ProxyCommand 옵션이 하나 있어야 함")
        self.assertIn("ec2-user@1.2.3.4", pc[0])
        self.assertIn("/tmp/k.pem", pc[0])   # 점프도 같은 키
        self.assertIn("-W", pc[0])
        self.assertNotIn("ProxyJump=", " ".join(cmd))  # ProxyJump 은 쓰지 않음
        self.assertIn("BatchMode=yes", cmd)
        self.assertIn("mini_worker@127.0.0.1", cmd)
        self.assertIn("2222", cmd)
        self.assertEqual(cmd[-1], "계란보관함")  # 키워드는 원격 명령(마지막 인자)

    def test_missing_host_raises(self):
        del os.environ["AWS103_HOST"]
        with self.assertRaises(RuntimeError):
            miniclient.build_ssh_command("x", "/tmp/k.pem")


class ParseOutput(unittest.TestCase):
    def test_picks_last_json_line(self):
        out = "warning: something\n" + json.dumps({"outcome": "ok", "count": 60})
        self.assertEqual(miniclient.parse_output(out)["outcome"], "ok")

    def test_no_json_returns_error(self):
        self.assertEqual(miniclient.parse_output("no json here")["outcome"], "error")


class HumanMessage(unittest.TestCase):
    def test_ok_none_others_have_text(self):
        self.assertIsNone(miniclient.human_message("ok"))
        for oc in ("challenge", "http_error", "paused", "budget", "busy", "timeout", "transport"):
            self.assertTrue(miniclient.human_message(oc))


class SearchFlow(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ.update({"AWS103_HOST": "1.2.3.4", "SSH_KEY_FILE": "/dev/null"})

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)

    def test_bad_input_shortcircuits(self):
        r = miniclient.search("a;b")
        self.assertEqual(r["outcome"], "badinput")

    def test_success_parses_and_adds_message(self):
        payload = json.dumps({"query": "계란보관함", "outcome": "ok", "count": 60, "items": [{"name": "x"}]})
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=payload, stderr="")):
            r = miniclient.search("계란보관함")
        self.assertEqual(r["outcome"], "ok")
        self.assertEqual(r["count"], 60)
        self.assertIsNone(r["message"])  # ok 는 사람 메시지 없음

    def test_timeout_maps(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 60)):
            r = miniclient.search("계란보관함")
        self.assertEqual(r["outcome"], "timeout")
        self.assertTrue(r["message"])

    def test_transport_error_when_ssh_fails_no_stdout(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=255, stdout="", stderr="conn refused")):
            r = miniclient.search("계란보관함")
        self.assertEqual(r["outcome"], "transport")


class ApiEndpoint(unittest.TestCase):
    def setUp(self):
        import importlib
        os.environ.pop("APP_PASSWORD", None)
        import app as appmod
        importlib.reload(appmod)
        self.app = appmod
        self.client = appmod.app.test_client()

    def test_badinput_400(self):
        r = self.client.post("/api/search", json={"q": "a;b"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["outcome"], "badinput")

    def test_search_calls_miniclient(self):
        with mock.patch.object(self.app.miniclient, "search",
                               return_value={"outcome": "ok", "count": 1, "items": [{"name": "x"}]}) as m:
            r = self.client.post("/api/search", json={"q": "계란보관함"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["outcome"], "ok")
        m.assert_called_once_with("계란보관함")

    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz").get_json(), {"ok": True})


class ApiPasswordGate(unittest.TestCase):
    def setUp(self):
        import importlib
        os.environ["APP_PASSWORD"] = "secret1"
        import app as appmod
        importlib.reload(appmod)
        self.app = appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        os.environ.pop("APP_PASSWORD", None)

    def test_wrong_password_401(self):
        r = self.client.post("/api/search", json={"q": "계란보관함", "password": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_right_password_passes(self):
        with mock.patch.object(self.app.miniclient, "search",
                               return_value={"outcome": "ok", "items": []}):
            r = self.client.post("/api/search", json={"q": "계란보관함", "password": "secret1"})
        self.assertEqual(r.status_code, 200)


class MarketReport(unittest.TestCase):
    def setUp(self):
        import importlib, os
        os.environ.pop("APP_PASSWORD", None)
        import app as appmod; importlib.reload(appmod)
        self.app = appmod; self.client = appmod.app.test_client()

    def test_api_markets_calls_miniclient(self):
        from unittest import mock
        fake = {"generated": "t", "markets": [{"keyword": "계란보관함", "opportunity": 0.6,
                 "dome_exists": 1, "dome_count": 42, "latest": {"review_sum": 1200},
                 "rarity": .7, "demand": .6, "steadiness": .5, "trend": []}]}
        with mock.patch.object(self.app.miniclient, "market_report", return_value=fake) as m:
            r = self.client.get("/api/markets")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["markets"][0]["keyword"], "계란보관함")
        m.assert_called_once()

    def test_markets_page_renders(self):
        r = self.client.get("/markets")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"markets", r.data.lower())

    def test_guide_page_renders(self):
        r = self.client.get("/guide")
        self.assertEqual(r.status_code, 200)
        self.assertIn("지표 설명".encode("utf-8"), r.data)


if __name__ == "__main__":
    unittest.main()
