"""Job API authorization and SSH framing, without network requests."""

import base64
import importlib
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import miniclient


class JobAPI(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"APP_PASSWORD": "test-pass"})
        self.env.start()
        self.addCleanup(self.env.stop)
        import app

        self.app = importlib.reload(app)
        self.client = self.app.app.test_client()
        self.request_id = str(uuid.uuid4())

    def test_both_start_and_status_require_password(self):
        self.assertEqual(self.client.post("/api/jobs", json={"q": "키워드"}).status_code, 401)
        self.assertEqual(self.client.get("/api/jobs/" + self.request_id).status_code, 401)

    def test_status_does_not_start_another_search(self):
        with (
            patch.object(
                miniclient, "get_job", return_value={"request_id": self.request_id, "state": "running"}
            ) as get,
            patch.object(miniclient, "start_job") as start,
        ):
            response = self.client.get("/api/jobs/" + self.request_id, headers={"X-App-Password": "test-pass"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        get.assert_called_once_with(self.request_id)
        start.assert_not_called()

    def test_submit_validates_non_string_and_forwards_id(self):
        headers = {"X-App-Password": "test-pass"}
        self.assertEqual(self.client.post("/api/jobs", json={"q": ["q"]}, headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs", json=["q"], headers=headers).status_code, 400)
        with patch.object(
            miniclient, "start_job", return_value={"request_id": self.request_id, "state": "queued"}
        ) as start:
            response = self.client.post(
                "/api/jobs", json={"q": "키워드", "request_id": self.request_id}, headers=headers
            )
        self.assertEqual(response.status_code, 202)
        start.assert_called_once_with("키워드", self.request_id)

    def test_payload_is_encoded_and_correlated(self):
        with (
            patch.object(miniclient, "_key_path", return_value="/tmp/existing-key"),
            patch.dict(os.environ, {"AWS103_HOST": "host", "SSH_KEY_FILE": "/tmp/existing-key"}),
            patch("subprocess.run", return_value=Mock(returncode=0, stdout='{"state":"queued"}', stderr="")) as run,
        ):
            result = miniclient.start_job("키워드", self.request_id)
        command = run.call_args.args[0][-1]
        prefix, encoded = command.split(" ", 1)
        self.assertEqual(prefix, "__worker__")
        self.assertEqual(json.loads(base64.urlsafe_b64decode(encoded))["request_id"], self.request_id)
        self.assertIn("transport_ms", result)

    def test_failed_job_is_a_readable_terminal_result(self):
        job = {"request_id": self.request_id, "state": "failed", "error": "worker_restarted"}
        with patch.object(miniclient, "get_job", return_value=job):
            response = self.client.get("/api/jobs/" + self.request_id, headers={"X-App-Password": "test-pass"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, job)

    def test_display_metrics_are_authenticated_bounded_and_sanitized(self):
        url = "/api/jobs/" + self.request_id + "/timing"
        self.assertEqual(self.client.post(url, json={}).status_code, 401)
        headers = {"X-App-Password": "test-pass"}
        for value in (-1, 180001, True, "10"):
            self.assertEqual(self.client.post(url, json={"elapsed_ms": value}, headers=headers).status_code, 400)
        with self.assertLogs("cpk.timing", level="WARNING") as logs:
            response = self.client.post(
                url,
                json={"elapsed_ms": 100, "first_result_ms": None, "password": "must-not-log", "cookies": "secret"},
                headers=headers,
            )
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("must-not-log", str(logs.output))
        self.assertNotIn("secret", str(logs.output))


if __name__ == "__main__":
    unittest.main()
