import json, os, unittest
from unittest import mock
import cpk_domeggook as dg

FIXTURE = json.dumps({"domeggook": {
    "header": {"numberOfItems": 8191},
    "list": {"item": [
        {"title": "니치상품", "price": "3200", "id": "seller1", "nick": "공급사", "url": "http://domeme.com/s/1"}
    ]}}}, ensure_ascii=False)

class BuildUrl(unittest.TestCase):
    def test_both_conditions_and_ver41(self):
        u = dg.build_url("계란보관함", "KEY", market="dome", premium=True, domestic=True, sz=1)
        self.assertIn("https://www.domeggook.com/ssl/api/", u)
        self.assertIn("ver=4.1", u); self.assertIn("mode=getItemList", u)
        self.assertIn("aid=KEY", u); self.assertIn("market=dome", u); self.assertIn("om=json", u)
        self.assertIn("sgd=true", u)      # 우수공급사
        self.assertIn("dfos=false", u)    # 국내배송(해외직배송 제외)
        self.assertIn("sz=1", u)
        self.assertIn("kw=%EA", u)        # 한글 URL 인코딩됨

    def test_no_filters_omits_params(self):
        u = dg.build_url("x", "KEY", premium=False, domestic=False)
        self.assertNotIn("sgd=", u); self.assertNotIn("dfos=", u)

class ParseResponse(unittest.TestCase):
    def test_count_and_samples(self):
        r = dg.parse_response(FIXTURE)
        self.assertEqual(r["count"], 8191)
        self.assertEqual(r["samples"][0]["seller"], "공급사")

    def test_single_item_as_dict(self):
        body = json.dumps({"domeggook": {"header": {"numberOfItems": 1},
                                         "list": {"item": {"title": "t", "id": "s", "url": "u"}}}})
        self.assertEqual(dg.parse_response(body)["count"], 1)
        self.assertEqual(len(dg.parse_response(body)["samples"]), 1)

    def test_zero_results(self):
        body = json.dumps({"domeggook": {"header": {"numberOfItems": 0}, "list": {}}})
        r = dg.parse_response(body)
        self.assertEqual(r["count"], 0); self.assertEqual(r["samples"], [])

    def test_error_is_not_an_empty_success(self):
        with self.assertRaises(ValueError):
            dg.parse_response('{"domeggook":{"error":{"message":"invalid key"}}}')

class CheckExistence(unittest.TestCase):
    def test_exists_true_via_injected_opener(self):
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return FIXTURE.encode("utf-8")
        with mock.patch.dict(os.environ, {"CPK_DOMEGGOOK_KEY": "KEY"}):
            r = dg.check_existence("계란보관함", opener=lambda url, timeout=0: FakeResp())
        self.assertTrue(r["exists"]); self.assertEqual(r["count"], 8191)
        self.assertEqual(r["market"], "dome")
