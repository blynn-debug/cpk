"""Provider adapter contract tests with synthetic HTTP responses only."""

import base64
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from cpk_benchmark import contract, summarize
from cpk_metrics import Trace
from cpk_providers import ZyteSearch


class ProviderContract(unittest.TestCase):
    def test_managed_data_uses_same_parser_and_autocomplete_contract(self):
        html = (Path(__file__).parent / "fixtures/search_sample.html").read_text(encoding="utf-8")
        payloads = []

        def opener(request, **kwargs):
            payload = json.loads(request.data)
            payloads.append(payload)
            if payload.get("browserHtml"):
                response = {"browserHtml": html, "url": payload["url"], "statusCode": 200}
            else:
                response = {
                    "httpResponseBody": base64.b64encode(b'[{"keyword":"suggestion"}]').decode(),
                    "statusCode": 200,
                }
            return io.BytesIO(json.dumps(response).encode())

        with patch.dict(os.environ, {"CPK_ZYTE_API_KEY": "test-key"}), patch("cpk_session.admit_request"):
            provider = ZyteSearch(opener=opener)
            result = provider.search("q", Trace())
            ac = provider.autocomplete("q", Trace())
        result.update(autocomplete=ac["items"], autocomplete_ok=ac["ok"])
        self.assertTrue(contract(result)["passed"])
        self.assertEqual(payloads[0]["geolocation"], "KR")
        self.assertEqual(len(payloads), 2)

    def test_no_credentials_fail_before_network(self):
        with patch.dict(os.environ, {"CPK_ZYTE_API_KEY": ""}):
            with self.assertRaises(ValueError):
                ZyteSearch(opener=lambda *args: self.fail("network request"))

    def test_missing_keywords_do_not_count_as_success(self):
        self.assertFalse(contract({"outcome": "ok", "items": []})["passed"])

    def test_small_samples_do_not_claim_p95(self):
        row = {"contract": {"passed": True}, "timing": {"elapsed_ms": 10, "milestones": {"essential_ready_ms": 5}}}
        for size in (0, 5, 99, 100, 101):
            with self.subTest(size=size):
                summary = summarize([row] * size)
                self.assertEqual(summary["successes"], size)
                self.assertEqual(summary["essential_p95_ms"], 5 if size >= 100 else None)


if __name__ == "__main__":
    unittest.main()
