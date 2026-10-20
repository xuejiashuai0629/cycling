import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config import ROOT, redact
import diagnostics
from tests.fixtures import prepared
from tools import KnowledgeError


class DiagnosticsTests(unittest.TestCase):
    def test_materials_summary_keeps_failure_without_private_response(self):
        value = {"status": "error", "message": "资料获取次数已达上限，请明天再尝试", "data": {"raw_response": "private-response", "media_id": "private-media"}}
        (ROOT / "outputs").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as directory, patch("diagnostics.ROOT", Path(directory)):
            diagnostics.save_materials_summary(value, "matebook gt 14")
            summary = json.loads((Path(directory) / ".ima-state" / "last_materials.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "error")
        self.assertIn("资料获取次数已达上限", summary["message"])
        self.assertEqual(summary["product"], "matebook gt 14")
        self.assertEqual(summary["source_count"], 0)
        self.assertNotIn("private", json.dumps(summary))

    def test_materials_logging_failure_does_not_break_read_result(self):
        with patch("pathlib.Path.write_text", side_effect=OSError("test-only write failure")):
            diagnostics.save_materials_summary({"status": "ok", "message": "读取成功", "data": {}}, "matebook gt 14")

    def test_configuration_does_not_claim_connected(self):
        with patch("diagnostics.settings", return_value={"IMA_OPENAPI_CLIENTID": "id", "IMA_OPENAPI_APIKEY": "key"}):
            self.assertTrue(diagnostics.ima_configured())
        with patch("diagnostics.settings", return_value={"IMA_OPENAPI_CLIENTID": "id"}):
            self.assertFalse(diagnostics.ima_configured())

    def test_live_summary_allowlists_response_without_ids_or_downloads(self):
        with patch("tools.ImaClient") as factory:
            client = factory.return_value
            client.knowledge_base.return_value = "private-kb"
            client.call.side_effect = [{"private-field": "private-key"}, {"info_list": [{"media_id": "private-media", "url": "https://invalid.test/private-url"}], "private": "do-not-display"}]
            value = diagnostics.check_ima_connection()
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["response_summary"], {"业务码": 0, "本页条目数": 1})
        self.assertIn("未逐份核对", value["scope"])
        self.assertNotIn("private", json.dumps(value))
        self.assertEqual(client.call.call_count, 2)

    def test_timeout_error_is_retryable_and_does_not_claim_success(self):
        with patch("tools.ImaClient", side_effect=KnowledgeError("ima读取超时")):
            value = diagnostics.check_ima_connection()
        self.assertEqual(value["status"], "error")
        self.assertNotIn("response_summary", value)

    def test_error_redaction_hides_credentials_ids_headers_and_signed_urls(self):
        with patch("config.settings", return_value={"IMA_OPENAPI_CLIENTID": "test-client", "IMA_OPENAPI_APIKEY": "test-ima-key", "DEEPSEEK_API_KEY": "test-model-key"}):
            value = redact('test-client test-ima-key test-model-key Authorization: Bearer fake-bearer media_id="fake-media" https://invalid.test/a?signature=fake-signature')
        for private in ("test-client", "test-ima-key", "test-model-key", "fake-bearer", "fake-media", "fake-signature"):
            self.assertNotIn(private, value)

    def test_saved_summary_has_reason_without_rejected_body_or_internal_fields(self):
        value = prepared()
        value.update(status="error", message="具体失败说明")
        value["data"].update(stage="blocked", draft={"body": "private-blocked-body"}, verification=[{"name": "平台格式", "state": "fail", "detail": "参数堆砌，缺少短段落"}])
        (ROOT / "outputs").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as directory, patch("diagnostics.ROOT", Path(directory)):
            diagnostics.save_review_summary(value)
            summary = (Path(directory) / ".ima-state" / "last_review.json").read_text(encoding="utf-8")
        self.assertIn("参数堆砌", summary)
        for private in ("private-blocked-body", "private-id", "private-url", "media_id"):
            self.assertNotIn(private, summary)
