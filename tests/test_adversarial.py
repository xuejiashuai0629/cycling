"""Offline adversarial fixtures, not real ima or model acceptance results."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import agent
import tools
import llm
from content_review import validate_review
from safety_review import draft_issues, evidence_issues
from tests.fixtures import TASK, DRAFT, REVIEW, prepared


class ProductIsolationTests(unittest.TestCase):
    def test_model_suffixes_and_mixed_titles_do_not_match(self):
        for title in ("MateBook D 14.md", "MateBook 14s.png", "MateBook 140.md", "MateBook 14 Pro.md", "MateBook 14 与 MateBook D 14 对比.md"):
            with self.subTest(title=title):
                self.assertFalse(tools.relevant("MateBook 14", title))
        for title in ("09-HUAWEI MateBook 14.md", "hwpc_matebook-14_屏幕.png", "MateBook 14 specs.md"):
            with self.subTest(title=title):
                self.assertTrue(tools.relevant("MateBook 14", title))
        self.assertFalse(tools.relevant("MateBook 14 2025", "MateBook 14 2024.md"))

    @patch("tools.ima_environment", return_value={})
    def test_wrong_file_cannot_inherit_correct_folder_model(self, _):
        client = tools.ImaClient()
        def pages(operation, body, **kwargs):
            if operation == "search_knowledge":
                return []
            return {
                None: [{"media_id": "right", "media_type": 99, "title": "MateBook 14"}],
                "right": [{"media_id": "nested", "media_type": 99, "title": "图片"}, {"media_id": "wrong", "media_type": 9, "title": "MateBook D 14.png"}],
                "nested": [{"media_id": "good", "media_type": 9, "title": "正面.png"}, {"media_id": "suffix", "media_type": 9, "title": "MateBook 14s.png"}],
            }[body.get("folder_id")]
        with patch.object(client, "pages", side_effect=pages):
            result = client.candidates("kb", "MateBook 14")
        self.assertEqual([item["media_id"] for item in result], ["good"])
        self.assertEqual(result[0]["folder_path"], "MateBook 14/图片")

    @patch("tools.ima_environment", return_value={})
    def test_empty_search_stops_before_any_writing(self, _):
        with patch.object(tools.ImaClient, "knowledge_base", return_value="kb"), patch.object(tools.ImaClient, "candidates", return_value=[]), patch("llm.generate") as write:
            result = agent.prepare_materials(TASK | {"product": "MateBook 999"})
        self.assertEqual(result["status"], "error")
        self.assertIn("不能据此断言库里没有", result["message"])
        write.assert_not_called()
        with patch("llm.generate") as write:
            result = agent.generate_content(result)
        self.assertEqual(result["status"], "error")
        write.assert_not_called()


class ProvenanceTests(unittest.TestCase):
    def test_observed_folders_are_exported_without_inventing_root_path(self):
        value = prepared()
        value["data"]["draft"] = deepcopy(DRAFT)
        value["data"]["images"][0].update(knowledge_path="MateBook GT 14/图片", path_note="仅为已浏览层级，不是完整路径")
        value["data"]["sources"][0].update(knowledge_path="产品库/MateBook GT 14", path_note="仅为已浏览层级")
        output = agent.export_markdown(value)
        self.assertIn("MateBook GT 14/图片", output)
        self.assertIn("产品库/MateBook GT 14", output)
        self.assertIn("仅为已浏览层级", output)
        self.assertNotIn("outputs/test/missing.png", output)
        self.assertNotIn("private-url", output)

    def test_unknown_knowledge_path_is_explicit(self):
        output = agent.export_markdown(prepared())
        self.assertIn("未取得库内目录路径", output)


class RedLineTests(unittest.TestCase):
    def bad_draft(self, phrase):
        draft = deepcopy(DRAFT)
        draft["body"] = DRAFT["body"].replace("\n\n【配图 I1】", "\n\n" + phrase + "\n\n【配图 I1】")
        return draft

    def test_known_red_lines_are_blocked_even_if_reviewer_approves(self):
        for phrase in ("开业大促直降500元。", "开业补贴五百。", "现货充足。", "我用了两周，亲测太香了。", "华为吊打联想小新。", "市面上最好的轻薄本。"):
            with self.subTest(phrase=phrase), patch("llm.generate", return_value=self.bad_draft(phrase)) as write, patch("llm.review", return_value=REVIEW):
                result = agent.generate_content(prepared())
            self.assertEqual(result["data"]["stage"], "rewriting")
            self.assertIsNone(result["data"]["draft"])
            self.assertEqual(write.call_count, 1)
            self.assertNotIn(phrase, agent.draft_markdown(result["data"]))

    def test_review_timeout_cannot_expose_locally_detected_violation(self):
        with patch("llm.generate", return_value=self.bad_draft("开业大促直降500元。")), patch("llm.review", side_effect=llm.ModelError("超时")):
            result = agent.generate_content(prepared())
        self.assertIsNone(result["data"]["draft"])
        self.assertEqual(result["data"]["stage"], "generation_paused")
        self.assertIn("正式资料", "；".join(result["data"]["review_issues"]))

    def test_empty_claims_cannot_hide_a_product_selling_point(self):
        draft = deepcopy(DRAFT) | {"claims": []}
        self.assertIn("产品事实", draft_issues(draft, prepared()["data"]))

    def test_parameter_omitted_from_claims_is_caught(self):
        issues = draft_issues(self.bad_draft("重量1.31kg。"), prepared()["data"])
        self.assertIn("产品事实", issues)
        self.assertTrue(any("1.31kg" in issue for issue in issues["产品事实"]))

    def test_claim_cannot_pair_fake_parameter_with_real_unrelated_quote(self):
        draft = self.bad_draft("重量1.31kg。")
        draft["claims"].append({"text": "重量1.31kg。", "source_id": "D1", "quote": DRAFT["claims"][0]["quote"]})
        self.assertIn("产品事实", draft_issues(draft, prepared()["data"]))

    def test_supported_numeric_fact_is_not_rejected(self):
        value = prepared()["data"]
        value["sources"][0]["text"] += "\n离线示例重量1.31kg，不是真实产品数据。"
        draft = self.bad_draft("重量1.31kg。")
        draft["claims"].append({"text": "重量1.31kg。", "source_id": "D1", "quote": "离线示例重量1.31kg，不是真实产品数据。"})
        self.assertNotIn("产品事实", draft_issues(draft, value))

    def test_formal_price_quote_is_distinct_from_user_request(self):
        value = prepared()["data"]
        value["sources"][0]["text"] += "\n离线假活动：开业大促直降500元；仅为测试。"
        draft = self.bad_draft("开业大促直降500元。")
        draft["claims"].append({"text": "开业大促直降500元。", "source_id": "D1", "quote": "开业大促直降500元；仅为测试。"})
        self.assertNotIn("营销合规", draft_issues(draft, value))
        # Removing the claim leaves only a request, not documentary evidence.
        draft["claims"].pop()
        self.assertIn("营销合规", draft_issues(draft, value))

    def test_scene_suggestion_is_not_personal_testimony(self):
        draft = self.bad_draft("早八赶课前，可以先把课件整理好。欢迎到店咨询。")
        self.assertNotIn("营销合规", draft_issues(draft, prepared()["data"]))

    def test_explicit_wrong_image_overrides_model_pending_or_pass(self):
        value = prepared()["data"]
        value["task"]["product"] = "MateBook 14"
        value["images"][0]["title"] = "MateBook D 14.png"
        checks = {item["name"]: item for item in validate_review(REVIEW, value, DRAFT)}
        self.assertEqual(checks["图片型号"]["state"], "fail")
        # An unavailable visual check stays pending for a plausible title.
        value["images"][0]["title"] = "MateBook 14.png"
        checks = {item["name"]: item for item in validate_review(REVIEW, value, DRAFT)}
        self.assertEqual(checks["图片型号"]["state"], "pending")

    def test_rewrite_receives_local_red_line_and_can_remove_it(self):
        with patch("llm.generate", side_effect=[self.bad_draft("开业大促直降500元。"), DRAFT]) as write, patch("llm.review", return_value=REVIEW):
            result = agent.generate_content(prepared())
            self.assertEqual(result["data"]["stage"], "rewriting")
            result = agent.generate_content(result, resume=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(write.call_count, 2)
        self.assertTrue(any("正式资料" in item for item in write.call_args.kwargs["feedback"]))
        self.assertNotIn("直降", result["data"]["draft"]["body"])


class ConflictTests(unittest.TestCase):
    def value(self, first="1.31kg", second="1.40kg"):
        value = prepared()
        value["data"]["sources"] = [
            {"source_id": "D1", "title": "离线参数甲.md", "knowledge_base": "测试库", "text": "重量：" + first},
            {"source_id": "D2", "title": "离线参数乙.md", "knowledge_base": "测试库", "text": "重量：" + second},
        ]
        return value

    def test_numeric_conflict_stops_before_writer_and_reports_both_sources(self):
        with patch("llm.generate") as write:
            result = agent.generate_content(self.value())
        write.assert_not_called()
        self.assertIsNone(result["data"]["draft"])
        self.assertEqual(result["data"]["stage"], "blocked")
        for title in ("离线参数甲.md", "离线参数乙.md"):
            self.assertIn(title, "；".join(result["data"]["review_issues"]))

    def test_equivalent_weight_units_are_not_a_conflict(self):
        self.assertEqual(evidence_issues(self.value("1.31kg", "1310g")["data"]), [])

    def test_wrong_model_source_is_rejected_before_writing(self):
        value = prepared()
        value["data"]["sources"][0]["title"] = "MateBook D 14.md"
        with patch("llm.generate") as write:
            result = agent.generate_content(value)
        self.assertEqual(result["status"], "error")
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
