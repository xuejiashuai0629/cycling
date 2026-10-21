from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import agent
from content_review import platform_format_issues, validate_draft, validate_review
import llm
from tests.fixtures import DRAFT, REVIEW, prepared, sample_png
from config import OUTPUTS, ROOT


class ContentTests(unittest.TestCase):
    def test_block_message_names_actual_failed_check_instead_of_all_categories(self):
        data = prepared()["data"]
        data["verification"] = [{"name": "平台图数", "state": "pass", "detail": "1张符合要求"}, {"name": "场景匹配", "state": "fail", "detail": "只替换了场景名，正文仍为通用模板"}]
        message = agent.blocked_message(data)
        self.assertIn("场景匹配", message)
        self.assertIn("通用模板", message)
        self.assertNotIn("平台图数", message)

    def test_invented_quotes_and_unknown_sources_are_rejected(self):
        for change in ({"quote": "不存在的原文"}, {"source_id": "D99"}, {"source_id": []}, {"text": "文案中不存在的句子"}):
            draft = deepcopy(DRAFT)
            draft["claims"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_draft(draft, prepared()["data"])

    def test_layout_differences_do_not_break_valid_evidence(self):
        draft = deepcopy(DRAFT)
        draft["claims"][0].update({
            "text": "提供  资料整理\n功能。",
            "quote": "提供  资料整理功能。",
        })
        draft["body"] = draft["body"].replace("提供资料整理功能。", "提供\t资料整理功能。")
        validate_draft(draft, prepared()["data"])

    def test_unknown_or_duplicate_images_are_rejected(self):
        for images in ([{"source_id": "I99", "position": "正文", "description": "候选"}], [DRAFT["image_plan"][0]] * 2):
            with self.assertRaises(ValueError):
                validate_draft(DRAFT | {"image_plan": images}, prepared()["data"])

    def test_review_cannot_claim_image_verification_and_count_is_local(self):
        checks = validate_review(REVIEW, prepared()["data"], DRAFT | {"image_plan": []})
        by_name = {item["name"]: item for item in checks}
        self.assertEqual(by_name["图片型号"]["state"], "pending")
        self.assertEqual(by_name["平台图数"]["state"], "fail")

    def test_conflict_blocks_display_and_export_of_draft(self):
        review = deepcopy(REVIEW)
        review.update(decision="blocked", issues=["原文冲突，需人工确认。"])
        source = prepared()
        original = deepcopy(source)
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=review):
            result = agent.generate_content(source)
        self.assertEqual(source, original)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["data"]["draft"])
        export = agent.export_markdown(result)
        self.assertNotIn(DRAFT["title"], export)
        self.assertIn("原文冲突", export)

    def test_review_timeout_preserves_private_candidate_and_resumes_review_only(self):
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", side_effect=llm.ModelError("等待超时")):
            failed = agent.generate_content(prepared())
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["data"]["stage"], "generation_paused")
        self.assertIsNone(failed["data"]["draft"])
        self.assertNotIn(DRAFT["title"], agent.export_markdown(failed))
        with patch("llm.generate") as write, patch("llm.review", return_value=REVIEW):
            result = agent.generate_content(failed, resume=True)
        write.assert_not_called()
        self.assertEqual(result["status"], "ok")
        exported = agent.export_markdown(result)
        for part in ("一、场景判断", "二、知识库依据", "三、图文软文", "四、核验结果", DRAFT["title"], DRAFT["claims"][0]["quote"]):
            self.assertIn(part, exported)
        self.assertNotIn("private-id", exported)
        self.assertNotIn("private-url", exported)

    def test_mock_cannot_be_used_for_real_generation(self):
        value = prepared()
        value["data"]["stage"] = "mock"
        with patch("llm.generate") as generate:
            result = agent.generate_content(value)
        self.assertEqual(result["status"], "error")
        generate.assert_not_called()

    def test_reviewer_feedback_returns_to_writer_with_sources(self):
        first = deepcopy(REVIEW)
        first["revision_requests"] = ["改为围绕老师周末同步课件的需求切入。"]
        fixed = deepcopy(DRAFT) | {"angle": "老师周末到镇上同步课件前，先整理备课资料"}
        with patch("llm.generate", side_effect=[DRAFT, fixed]) as write, patch("llm.review", side_effect=[first, REVIEW]) as review:
            result = agent.generate_content(prepared())
            self.assertEqual(result["data"]["stage"], "rewriting")
            self.assertIsNone(result["data"]["draft"])
            result = agent.generate_content(result, resume=True)
        self.assertEqual(write.call_count, 2)
        self.assertEqual(review.call_count, 2)
        self.assertIn(first["revision_requests"][0], write.call_args.kwargs["feedback"])
        self.assertEqual(write.call_args.kwargs["previous_draft"], DRAFT)
        self.assertEqual(result["data"]["draft"], fixed)
        self.assertEqual(result["data"]["rewrite_count"], 1)

    def test_rewrites_continue_past_two_until_review_passes(self):
        failing = deepcopy(REVIEW)
        failing["revision_requests"] = ["场景切入点仍不够贴合，请调整。"]
        failing["checks"][4].update(state="fail", detail="未回应具体备课场景。")
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", side_effect=[failing] * 5 + [REVIEW]) as review:
            result = agent.generate_content(prepared())
            for _ in range(5):
                self.assertEqual(result["data"]["stage"], "rewriting")
                self.assertIsNone(result["data"]["draft"])
                self.assertNotIn(DRAFT["title"], agent.export_markdown(result))
                self.assertIn("场景切入点仍不够贴合", "；".join(result["data"]["review_issues"]))
                result = agent.generate_content(result, resume=True)
        self.assertEqual(write.call_count, 6)
        self.assertEqual(review.call_count, 6)
        self.assertEqual(result["data"]["rewrite_count"], 5)
        self.assertEqual(result["data"]["draft"], DRAFT)
        self.assertEqual(len(result["data"]["review_history"]), 6)
        self.assertNotIn("_rewrite", result["data"])

    def test_external_pending_does_not_trigger_useless_rewrites(self):
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", return_value=REVIEW):
            result = agent.generate_content(prepared())
        write.assert_called_once()
        self.assertEqual(result["data"]["rewrite_count"], 0)
        self.assertNotEqual(result["data"]["stage"], "pass")

    def test_unresolved_revision_requests_keep_job_running(self):
        review = deepcopy(REVIEW)
        review["revision_requests"] = ["建议把开头的备课问题说明得更具体。"]
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", return_value=review):
            result = agent.generate_content(prepared())
        self.assertEqual(write.call_count, 1)
        self.assertEqual(result["data"]["stage"], "rewriting")
        self.assertIsNone(result["data"]["draft"])

    def test_repeated_model_format_failure_cannot_escape_local_structure_gate(self):
        review = deepcopy(REVIEW)
        review["checks"][3].update(state="fail", detail="朋友圈语气和篇幅仍不符合短文要求。")
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=review):
            result = agent.generate_content(prepared())
        self.assertEqual(result["data"]["stage"], "rewriting")
        self.assertIsNone(result["data"]["draft"])

    def test_known_bad_format_is_hidden_even_if_model_approves(self):
        malformed = deepcopy(DRAFT) | {"selling_points": []}
        with patch("llm.generate", return_value=malformed) as write, patch("llm.review", return_value=REVIEW):
            result = agent.generate_content(prepared())
        self.assertEqual(write.call_count, 1)
        self.assertEqual(result["data"]["stage"], "rewriting")
        self.assertIsNone(result["data"]["draft"])
        self.assertNotIn(DRAFT["title"], agent.export_markdown(result))

    def test_image_shortage_is_reported_without_making_up_images(self):
        source = prepared()
        source["data"]["task"]["platform"] = "小红书"
        source["data"]["strategy"] = agent.strategy_for(source["data"]["task"])
        draft = deepcopy(DRAFT)
        draft["body"] = "提供资料整理功能。\n\n从课件整理的场景出发。\n\n【配图 I1】"
        with patch("llm.generate", return_value=draft) as write, patch("llm.review", return_value=REVIEW):
            result = agent.generate_content(source)
        write.assert_not_called()
        self.assertEqual(result["data"]["stage"], "awaiting_materials")
        self.assertIn("需补充", "；".join(result["data"]["review_issues"]))

    def test_missing_or_corrupt_candidates_stop_before_model_calls_on_all_platforms(self):
        for platform in ("公众号", "小红书", "门店朋友圈", "门店海报"):
            for content in (None, b"\x89PNG\r\n\x1a\nspoofed header"):
                with self.subTest(platform=platform, content=content), TemporaryDirectory(dir=OUTPUTS) as directory:
                    source = prepared()
                    data = source["data"]
                    data["task"]["platform"] = platform
                    data["strategy"] = agent.strategy_for(data["task"])
                    path = Path(directory) / "unavailable.png"
                    if content:
                        path.write_bytes(content)
                    data["images"][0]["path"] = path.relative_to(ROOT).as_posix()
                    with patch("llm.generate") as write, patch("llm.review") as review:
                        result = agent.generate_content(source)
                    write.assert_not_called()
                    review.assert_not_called()
                    self.assertEqual(result["data"]["stage"], "awaiting_materials")
                    self.assertIsNone(result["data"]["draft"])
                    self.assertNotIn("_rewrite", result["data"])
                    self.assertIn("需补充", result["message"])

    def test_file_deleted_during_review_cannot_be_delivered(self):
        source = prepared()
        with TemporaryDirectory(dir=OUTPUTS) as directory:
            path = Path(directory) / "temporary.png"
            path.write_bytes(sample_png())
            source["data"]["images"][0]["path"] = path.relative_to(ROOT).as_posix()

            def delete_during_review(*args):
                path.unlink()
                return REVIEW

            with patch("llm.generate", return_value=DRAFT), patch("llm.review", side_effect=delete_during_review):
                result = agent.generate_content(source)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"]["stage"], "awaiting_materials")
        self.assertNotIn("_rewrite", result["data"])
        self.assertIsNone(result["data"]["draft"])
        self.assertNotIn(DRAFT["title"], agent.export_markdown(result))

    def test_invalid_extra_candidates_are_not_sent_to_writer(self):
        source = prepared()
        source["data"]["images"].append(source["data"]["images"][0] | {"source_id": "I2", "path": "outputs/missing.png"})
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", return_value=REVIEW):
            result = agent.generate_content(source)
        self.assertEqual([picture["source_id"] for picture in write.call_args.args[1]["images"]], ["I1"])
        self.assertEqual(result["data"]["draft"], DRAFT)

    def test_incomplete_review_cannot_claim_format_or_scene_passed(self):
        incomplete = deepcopy(REVIEW)
        incomplete["checks"] = incomplete["checks"][:4]
        with self.assertRaises(ValueError):
            validate_review(incomplete, prepared()["data"], DRAFT)

    def test_each_platform_has_its_own_structure(self):
        data = prepared()["data"]
        for platform in ("公众号", "小红书", "门店朋友圈", "门店海报"):
            item = deepcopy(data)
            item["task"]["platform"] = platform
            item["strategy"] = agent.strategy_for(item["task"])
            draft = deepcopy(DRAFT)
            if platform in ("公众号", "小红书"):
                self.assertTrue(platform_format_issues(draft, item))
                draft["body"] = "提供资料整理功能。\n\n围绕老师整理课件的问题组织内容。\n\n【配图 I1】"
            if platform == "门店海报":
                self.assertTrue(platform_format_issues(draft, item))
                draft["body"] = "- 提供资料整理功能。\n\n【配图 I1】"
            with self.subTest(platform=platform):
                self.assertEqual(platform_format_issues(draft, item), [])
        xhs = deepcopy(data)
        xhs["task"]["platform"] = "小红书"
        self.assertTrue(any("标签" in issue for issue in platform_format_issues(DRAFT | {"tags": []}, xhs)))
        poster = deepcopy(data)
        poster["task"]["platform"] = "门店海报"
        self.assertTrue(any("1–3" in issue for issue in platform_format_issues(DRAFT | {"selling_points": ["a", "b", "c", "d"]}, poster)))

    def test_image_markers_match_plan_and_export_stays_at_insertion_point(self):
        for body in ("提供资料整理功能。", "提供资料整理功能。【配图 I1】【配图 I1】", "提供资料整理功能。【配图 I99】"):
            self.assertTrue(any("插图" in issue for issue in platform_format_issues(DRAFT | {"body": body}, prepared()["data"])))
        data = prepared()["data"] | {"draft": DRAFT}
        text = agent.draft_markdown(data)
        self.assertIn("【配图 I1：离线测试图.png｜来源：测试库", text)
        self.assertLess(text.index("【配图 I1："), text.index(DRAFT["cta"]))
        self.assertIn("#备课", text)
        compact = deepcopy(DRAFT) | {"body": DRAFT["body"].replace("【配图 I1】", "【配图I1 】")}
        self.assertEqual(platform_format_issues(compact, data), [])
        self.assertIn("【配图 I1：", agent.draft_markdown(data | {"draft": compact}))

    def test_explicit_image_position_is_rendered_without_another_model_call(self):
        draft = deepcopy(DRAFT)
        draft["body"] = "提供资料整理功能。\n\n可以先整理课件。"
        draft["image_plan"][0]["position"] = "第1段后"
        original = deepcopy(draft)
        with patch("llm.generate", return_value=draft) as write, patch("llm.review", return_value=REVIEW) as review:
            result = agent.generate_content(prepared())
        write.assert_called_once()
        self.assertEqual(draft, original)
        self.assertEqual(result["data"]["draft"]["body"], "提供资料整理功能。\n\n【配图 I1】\n\n可以先整理课件。")
        self.assertEqual(review.call_args.args[1]["body"], result["data"]["draft"]["body"])
        self.assertNotIn("_rewrite", result["data"])

    def test_image_renderer_never_guesses_ambiguous_or_out_of_range_positions(self):
        for position in ("待核验后放置", "正文", "第99段后"):
            draft = deepcopy(DRAFT)
            draft["body"] = "提供资料整理功能。"
            draft["image_plan"][0]["position"] = position
            clean = validate_draft(draft, prepared()["data"])
            self.assertEqual(clean["body"], draft["body"])
            self.assertTrue(platform_format_issues(clean, prepared()["data"]))

    def test_xiaohongshu_images_are_inserted_at_each_explicit_text_paragraph(self):
        data = prepared()["data"]
        data["task"]["platform"] = "小红书"
        data["strategy"] = agent.strategy_for(data["task"])
        data["images"] = [data["images"][0] | {"source_id": f"I{index}"} for index in (1, 2, 3)]
        draft = deepcopy(DRAFT)
        paragraphs = ["提供资料整理功能。", "课件整理好再出门。", "可以按实际需求核对配置。"]
        draft["body"] = "\n\n".join(paragraphs)
        draft["image_plan"] = [DRAFT["image_plan"][0] | {"source_id": f"I{index}", "position": f"第{index}段后"} for index in (3, 1, 2)]
        clean = validate_draft(draft, data)
        self.assertEqual(platform_format_issues(clean, data), [])
        self.assertEqual(clean["body"].split("\n\n"), [part for index, text in enumerate(paragraphs, 1) for part in (text, f"【配图 I{index}】")])

    def test_image_renderer_does_not_hide_partial_or_duplicate_markers(self):
        for marker in ("【配图 I99】", "【配图 I1】【配图 I1】", "【配图 I1：待定】"):
            draft = deepcopy(DRAFT) | {"body": "提供资料整理功能。" + marker}
            clean = validate_draft(draft, prepared()["data"])
            self.assertEqual(clean["body"], draft["body"])
            self.assertTrue(platform_format_issues(clean, prepared()["data"]))

    def test_optional_review_suggestions_do_not_force_rewriting(self):
        review = deepcopy(REVIEW) | {"suggestions": ["可将欢迎咨询改为欢迎交流，更亲切。"]}
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", return_value=review):
            result = agent.generate_content(prepared())
        write.assert_called_once()
        self.assertEqual(result["data"]["draft"], DRAFT)
        self.assertNotIn("_rewrite", result["data"])
        self.assertIn("可选建议", agent.export_markdown(result))

    def test_review_timeout_never_exposes_malformed_platform_copy(self):
        malformed = deepcopy(DRAFT) | {"body": "提供资料整理功能。"}
        with patch("llm.generate", return_value=malformed), patch("llm.review", side_effect=llm.ModelError("超时")):
            result = agent.generate_content(prepared())
        self.assertIsNone(result["data"]["draft"])
        self.assertEqual(result["data"]["stage"], "generation_paused")

    def test_invalid_writer_output_is_sent_back_without_exporting_it(self):
        malformed = deepcopy(DRAFT)
        malformed["claims"][0]["quote"] = "捏造的引用"
        with patch("llm.generate", side_effect=[malformed, DRAFT]) as write, patch("llm.review", return_value=REVIEW) as review:
            first = agent.generate_content(prepared())
            self.assertEqual(first["data"]["stage"], "rewriting")
            self.assertNotIn(DRAFT["title"], agent.export_markdown(first))
            review.assert_not_called()
            result = agent.generate_content(first, resume=True)
        self.assertIn("事实摘录", write.call_args.kwargs["feedback"][0])
        self.assertEqual(result["data"]["draft"], DRAFT)

    def test_invalid_review_retries_reviewer_without_rewriting(self):
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", side_effect=[{}, REVIEW]):
            first = agent.generate_content(prepared())
            self.assertEqual(first["data"]["stage"], "review_retry")
            self.assertIsNone(first["data"]["draft"])
            result = agent.generate_content(first, resume=True)
        write.assert_called_once()
        self.assertEqual(result["data"]["draft"], DRAFT)

    def test_layout_failure_is_rewritten_even_if_reviewer_labels_it_blocked(self):
        failing = deepcopy(REVIEW)
        failing.update(decision="blocked", revision_requests=["改为自然短段落。"])
        failing["checks"][3].update(state="fail", detail="像参数卡，需换成口语场景。")
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", side_effect=[failing, REVIEW]):
            first = agent.generate_content(prepared())
            self.assertEqual(first["data"]["stage"], "rewriting")
            result = agent.generate_content(first, resume=True)
        self.assertEqual(result["data"]["draft"], DRAFT)

    def test_revise_without_actionable_reason_cannot_release_copy(self):
        incomplete = deepcopy(REVIEW) | {"revision_requests": [], "pending_items": [], "issues": ["需要修改"]}
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=incomplete):
            result = agent.generate_content(prepared())
        self.assertEqual(result["data"]["stage"], "review_retry")
        self.assertIsNone(result["data"]["draft"])

    def test_resume_never_uses_feedback_from_another_task(self):
        review = deepcopy(REVIEW) | {"revision_requests": ["重写开头"]}
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=review):
            first = agent.generate_content(prepared())
        first["data"]["task"]["scenario"] = "企业采购"
        with patch("llm.generate") as write:
            result = agent.generate_content(first, resume=True)
        write.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertNotIn("_rewrite", result["data"])

    def test_pause_keeps_feedback_and_rounds_for_later_resume(self):
        review = deepcopy(REVIEW) | {"revision_requests": ["重写开头"]}
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=review):
            first = agent.generate_content(prepared())
        paused = agent.pause_generation(first)
        self.assertEqual(paused["data"]["stage"], "generation_paused")
        self.assertEqual(first["data"]["stage"], "rewriting")
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", return_value=REVIEW):
            result = agent.generate_content(paused, resume=True)
        self.assertIn("重写开头", write.call_args.kwargs["feedback"])
        self.assertEqual(result["data"]["rewrite_count"], 1)


if __name__ == "__main__":
    unittest.main()
