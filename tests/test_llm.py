import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

import llm
from tests.fixtures import TASK, DRAFT, prepared


def envelope(content="{}", reason="stop"):
    return json.dumps({"choices": [{"finish_reason": reason, "message": {"content": content}}]}).encode("utf-8")


class LlmTests(unittest.TestCase):
    def test_review_preserves_necessary_version_caveats(self):
        prompt = llm.prompt("review")
        self.assertIn("不能仅因保留此类条件就把平台格式判 fail", prompt)
        self.assertIn("不得误报为“营销合规”", prompt)

    def setUp(self):
        self.settings = patch("llm.settings", return_value={"DEEPSEEK_API_KEY": "offline-test-key"})
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def responder(self, raw):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = raw
        return opener

    def test_key_is_required_before_network(self):
        with patch("llm.settings", return_value={}), patch("llm.build_opener") as opener:
            with self.assertRaises(llm.ModelNotConfigured):
                llm.complete_json("JSON", {})
        opener.assert_not_called()

    def test_official_endpoint_json_mode_and_key_only_in_header(self):
        opener = self.responder(envelope('{"answer":"你好"}'))
        with patch("llm.build_opener", return_value=opener):
            self.assertEqual(llm.complete_json("test", {"question": "你好"}), {"answer": "你好"})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer offline-test-key")
        body = json.loads(request.data)
        self.assertNotIn("offline-test-key", request.data.decode("utf-8"))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertFalse(body["stream"])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 60)

    def test_empty_truncated_and_malformed_output_is_rejected(self):
        for raw in (envelope(""), envelope("{}", "length"), envelope("[]"), envelope("broken"), b"{}", envelope(None), b"x" * (2 * 1024 * 1024 + 1)):
            with self.subTest(size=len(raw)), patch("llm.build_opener", return_value=self.responder(raw)):
                with self.assertRaises(llm.ModelError):
                    llm.complete_json("JSON", {})

    def test_http_and_connection_errors_hide_private_response(self):
        errors = [HTTPError(llm.ENDPOINT, code, "private-server-detail", {}, io.BytesIO(b"private-response")) for code in (400, 401, 402, 404, 429, 500)] + [URLError("private-network-detail"), TimeoutError("private-detail"), OSError("private-detail")]
        for error in errors:
            opener = MagicMock()
            opener.open.side_effect = error
            with self.subTest(error=type(error)), patch("llm.build_opener", return_value=opener):
                with self.assertRaises(llm.ModelError) as raised:
                    llm.complete_json("JSON", {})
                self.assertNotIn("private", str(raised.exception))

    def test_roles_share_adapter_and_scene_references_are_used(self):
        data = prepared()["data"]
        with patch("llm.complete_json", return_value={}) as complete:
            llm.understand("需求", [], None)
            llm.generate(TASK, data)
            llm.review(data, DRAFT)
        self.assertEqual(complete.call_count, 3)
        for call in complete.call_args_list[:2]:
            for name in ("开学季", "职场办公", "内容创作", "家庭换新", "企业采购", "门店到访", "新品首发", "节日营销"):
                self.assertIn(name, call.args[0])
            self.assertIn("不是允许输入的场景清单", call.args[0])
        for call in complete.call_args_list[1:]:
            payload = json.dumps(call.args[1], ensure_ascii=False)
            self.assertNotIn("private-id", payload)
            self.assertNotIn("private-url", payload)

    def test_rewrite_keeps_full_evidence_and_sends_specific_feedback(self):
        data = prepared()["data"]
        with patch("llm.complete_json", return_value={}) as complete:
            llm.generate(TASK, data, previous_draft=DRAFT, feedback=["按朋友圈保留一个卖点"])
        payload = complete.call_args.args[1]
        self.assertEqual(payload["sources"][0]["text"], data["sources"][0]["text"])
        self.assertEqual(payload["previous_draft"], DRAFT)
        self.assertEqual(payload["revision_requests"], ["按朋友圈保留一个卖点"])
        self.assertNotIn("private-id", json.dumps(payload, ensure_ascii=False))

    def test_first_draft_receives_selected_platform_contract_before_writing(self):
        import agent
        for platform in ("公众号", "小红书", "门店朋友圈", "门店海报"):
            data = prepared()["data"]
            data["task"]["platform"] = platform
            data["strategy"] = agent.strategy_for(data["task"])
            with self.subTest(platform=platform), patch("llm.complete_json", return_value={}) as complete:
                llm.generate(data["task"], data)
            payload = complete.call_args.args[1]
            contract = payload["writing_contract"]
            self.assertEqual(contract["platform"], platform)
            self.assertEqual(contract["allowed_image_ids"], ["I1"])
            self.assertEqual(tuple(contract["image_count"].values()), data["strategy"]["images"])
            self.assertGreaterEqual(len(contract["outline"]), 4)
            self.assertNotIn("previous_draft", payload)
            self.assertEqual(contract["minimum_paragraphs"], 2 if platform in ("公众号", "小红书") else 1)


if __name__ == "__main__":
    unittest.main()
