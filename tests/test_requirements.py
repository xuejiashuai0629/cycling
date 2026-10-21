from copy import deepcopy
import unittest
from unittest.mock import patch

import agent
from config import PLATFORMS
from requirements_review import next_state, normalize_task
from tests.fixtures import TASK, analysis, unclear


class RequirementsTests(unittest.TestCase):
    def test_broad_scene_is_clarified_even_if_model_marks_it_clear(self):
        for scene in ("学习", "用于学习", "日常学习和办公", "大学生在图书馆学习", "在办公室办公", "办公", "设计", "内容创作"):
            with self.subTest(scene=scene):
                value = analysis()
                value["requirements"]["scenario"]["value"] = scene
                result = next_state(value, None, PLATFORMS)
                self.assertEqual(result["stage"], "clarifying")
                self.assertEqual(result["question_fields"], ["scenario"])
                self.assertEqual(result["task"]["scenario"], scene)
                self.assertIn("具体要用电脑完成什么任务", result["question"])
                self.assertIn("软件", result["question"])

    def test_specific_workloads_remain_free_text_and_can_be_confirmed(self):
        for scene in ("用WPS写课程论文，同时开十几个PDF查文献，常带去图书馆", "用剪映剪辑社团活动的4K视频，每段约20分钟", "不知道软件名，要把课堂笔记整理成文档并查资料"):
            value = analysis()
            value["requirements"]["scenario"]["value"] = scene
            result = next_state(value, None, PLATFORMS)
            self.assertEqual(result["stage"], "ready_for_confirmation")
            self.assertEqual(result["task"]["scenario"], scene)

    def test_broad_scene_cannot_bypass_chat_by_direct_material_request(self):
        with patch("agent.collect_sources") as ima, patch("local_materials.collect_local_sources") as local:
            for mode in ("ima", "local"):
                result = agent.prepare_materials(TASK | {"scenario": "学习"}, mode=mode)
                self.assertEqual(result["status"], "error")
                self.assertIn("使用场景还不具体", result["message"])
        ima.assert_not_called()
        local.assert_not_called()

    def test_non_answers_cannot_be_marked_clear_by_model(self):
        for field in ("audience", "scenario", "platform", "word_count", "image_count", "price_range", "tone"):
            value = analysis()
            value["requirements"][field] = {"state": "clear", "value": "都行"}
            result = next_state(value, None, PLATFORMS)
            self.assertEqual(result["stage"], "clarifying")
            self.assertIn(field, result["question_fields"])

    def test_free_scenario_is_preserved_and_awaits_confirmation(self):
        value = analysis()
        original = deepcopy(value)
        result = next_state(value, None, PLATFORMS)
        self.assertEqual(result["stage"], "ready_for_confirmation")
        self.assertEqual(result["task"], TASK)
        self.assertEqual(value, original)

    def test_all_missing_fields_are_asked_together(self):
        value = unclear()
        value["requirements"]["audience"] = {"state": "missing", "value": ""}
        result = next_state(value, None, PLATFORMS)
        self.assertEqual(result["stage"], "clarifying")
        self.assertEqual(result["question_fields"], ["audience", "scenario"])
        self.assertIn("目标人群", result["question"])
        self.assertIn("使用场景", result["question"])
        with self.assertRaises(ValueError):
            next_state(unclear() | {"questions": {"scenario": ""}}, None, PLATFORMS)

    def test_no_round_limit_or_assumed_values(self):
        state = next_state(unclear(), None, PLATFORMS)
        for turn in range(20):
            state = next_state(unclear(), state, PLATFORMS)
            self.assertEqual(state["rounds"], turn + 2)
            self.assertEqual(state["stage"], "clarifying")
            self.assertEqual(state["task"]["scenario"], "待定")
            self.assertNotIn("assumptions", state)
        final = next_state(analysis(), state, PLATFORMS)
        self.assertEqual(final["stage"], "ready_for_confirmation")

    def test_goal_is_optional_and_long_history_does_not_stop_clarification(self):
        value = analysis()
        value["requirements"]["goal"] = {"state": "missing", "value": ""}
        self.assertEqual(next_state(value, None, PLATFORMS)["stage"], "ready_for_confirmation")
        history = [{"role": "user", "content": "仍未明确"}] * 90
        with patch("llm.understand", return_value=unclear()) as understand:
            result = agent.chat("继续补充", history, state={"rounds": 45})
        self.assertEqual(result["data"]["stage"], "clarifying")
        self.assertEqual(len(understand.call_args.args[1]), 60)
        self.assertEqual(len(history), 90)

    def test_unknown_platform_must_be_confirmed(self):
        value = analysis()
        value["requirements"]["platform"]["value"] = "其他平台"
        result = next_state(value, None, PLATFORMS)
        self.assertEqual(result["question_fields"], ["platform"])
        self.assertEqual(result["requirements"]["platform"]["state"], "ambiguous")

    def test_bad_checklists_and_types_are_rejected(self):
        values = [None, {}, analysis(requirements={}), analysis(extra=["wrong"])]
        empty_clear = analysis()
        empty_clear["requirements"]["goal"]["value"] = ""
        values.append(empty_clear)
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                next_state(value, None, PLATFORMS)
        for value in (False, [], None, "长" * 3001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_task(TASK | {"scenario": value})

    def test_suspected_gt4_is_not_silently_rewritten_to_gt14(self):
        with patch("llm.understand", return_value=analysis()), patch("agent.collect_sources") as collect:
            result = agent.chat("产品是 MateBook GT 4", [])
        self.assertEqual(result["data"]["task"]["product"], "MateBook GT 4")
        self.assertEqual(result["data"]["requirements"]["product"]["state"], "ambiguous")
        self.assertIn("是否", result["message"])
        collect.assert_not_called()

    def test_gt14_does_not_trigger_gt4_heuristic_and_history_is_read_only(self):
        history = [{"role": "user", "content": "给老师写"}]
        snapshot = deepcopy(history)
        with patch("llm.understand", return_value=analysis()) as understand:
            result = agent.chat("MateBook GT 14", history)
        self.assertEqual(result["data"]["stage"], "ready_for_confirmation")
        self.assertEqual(history, snapshot)
        self.assertEqual(understand.call_args.args[1], history)
        with patch("llm.understand", return_value=analysis()):
            corrected = agent.chat("之前写了 MateBook GT 4，现在更正为 MateBook GT 14", history)
        self.assertEqual(corrected["data"]["task"]["product"], "MateBook GT 14")

    def test_failed_model_checklist_does_not_trigger_search(self):
        with patch("llm.understand", return_value={}), patch("agent.collect_sources") as collect:
            self.assertEqual(agent.chat("帮我写", [])["status"], "error")
        collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
