from copy import deepcopy
import subprocess
import unittest
from unittest.mock import patch

import agent
import frontend_mock
import llm
import tools
from tests.fixtures import test_image_path

TASK = {"product": "MateBook GT 14", "audience": "内容创作者", "scenario": "用剪映整理并剪辑社团活动的1080p短视频", "platform": "门店朋友圈", "word_count": "约 120 字", "image_count": "1 张", "price_range": "4000–6000 元", "goal": "", "extra": ""}


class AgentTests(unittest.TestCase):
    def test_material_read_failure_preserves_specific_reason(self):
        reason = "本次未读到该型号的可用文字资料，不能据此断言库里没有。具体原因：资料甲.md：文档原文为空。"
        with patch("agent.runtime_issues", return_value=[]), patch("agent.collect_sources", side_effect=tools.KnowledgeError(reason)):
            result = agent.prepare_materials(TASK)
        self.assertEqual(result["status"], "error")
        self.assertIn("资料甲.md：文档原文为空", result["message"])

    def test_quota_error_is_not_presented_as_wrong_or_missing_model(self):
        with patch("agent.runtime_issues", return_value=[]), patch("agent.collect_sources", side_effect=tools.KnowledgeError("资料获取次数已达上限，请明天再尝试")), patch("frontend_mock.prepare_materials") as mock:
            result = agent.prepare_materials({**TASK, "product": "matebook gt 14"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"], {})
        self.assertIn("ima 原文读取额度已用尽", result["message"])
        self.assertIn("资料获取次数已达上限，请明天再尝试", result["message"])
        self.assertNotIn("核对型号", result["message"])
        mock.assert_not_called()

    def test_real_ima_failure_never_falls_back_to_local_test_materials(self):
        with patch("agent.runtime_issues", return_value=[]), patch("agent.collect_sources", side_effect=tools.KnowledgeError("知识库连接失败")) as collect, patch("frontend_mock.prepare_materials") as mock:
            result = agent.prepare_materials(TASK)
        collect.assert_called_once()
        mock.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"], {})
        self.assertIn("知识库连接失败", result["message"])

    def test_missing_information_asks_for_one_field_without_network(self):
        with patch("agent.collect_sources") as collect:
            result = agent.prepare_materials({})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "请先补充目标人群。")
        collect.assert_not_called()

    def test_platform_and_scenario_change_strategy(self):
        first = agent.strategy_for(TASK)
        second = agent.strategy_for({**TASK, "platform": "公众号", "scenario": "职场办公"})
        self.assertNotEqual(first["images"], second["images"])
        self.assertNotEqual(first["structure"], second["structure"])
        self.assertNotEqual(first["focus"], second["focus"])

    def test_mock_never_claims_real_sources_in_export(self):
        result = frontend_mock.prepare_materials(TASK)
        exported = agent.export_markdown(result)
        self.assertIn("联调模拟", exported)
        self.assertNotIn("来源：任德耀", exported)
        self.assertNotIn("media_id", exported)

    def test_missing_model_returns_error_and_preserves_materials(self):
        prepared = frontend_mock.prepare_materials(TASK)
        prepared["data"]["stage"] = "materials_ready"
        prepared["data"]["images"] = [{"source_id": "I1", "title": "测试图.png", "path": test_image_path()}]
        original = deepcopy(prepared)
        with patch("llm.settings", return_value={}):
            generated = agent.generate_content(prepared)
        self.assertEqual(generated["status"], "error")
        self.assertIn("尚未配置 DeepSeek", generated["message"])
        self.assertEqual(prepared, original)
        self.assertIsNone(generated["data"]["draft"])

    def test_chat_does_not_modify_history(self):
        history = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}]
        original = deepcopy(history)
        with patch("llm.understand", side_effect=llm.ModelError("模型测试失败")):
            self.assertEqual(agent.chat("写文案", history)["status"], "error")
        self.assertEqual(history, original)


class ImaTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch("tools.ima_environment", return_value={"PYTHONIOENCODING": "utf-8"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_business_error_is_not_a_successful_process(self):
        completed = subprocess.CompletedProcess([], 0, '{"code":220030,"msg":"该文件获取失败","data":{}}', "")
        with patch("tools.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(tools.KnowledgeError, "该文件获取失败"):
                tools.ImaClient().call("get_media_info", {"media_id": "test"})

    def test_official_quota_response_stops_after_first_media_request(self):
        client = tools.ImaClient()
        documents = [{"title": f"MateBook GT 14 资料{i}.md", "media_type": 7, "media_id": str(i)} for i in range(3)]
        completed = subprocess.CompletedProcess([], 0, '{"code":110011,"msg":"资料获取次数已达上限，请明天再尝试","data":{}}', "")
        with patch("tools.ImaClient", return_value=client), patch.object(client, "knowledge_base", return_value="test-kb"), patch.object(client, "candidates", return_value=documents), patch("tools.subprocess.run", return_value=completed) as run:
            with self.assertRaisesRegex(tools.KnowledgeError, "资料获取次数已达上限"):
                tools.collect_sources(TASK["product"], "offline_quota_check", 1)
        self.assertEqual(run.call_count, 1)

    def test_file_read_error_retains_cause_and_redacts_private_details(self):
        client = tools.ImaClient()
        documents = [{"title": "MateBook GT 14 资料.md", "media_type": 7, "media_id": "test-media"}]
        with patch("tools.ImaClient", return_value=client), patch.object(client, "knowledge_base", return_value="test-kb"), patch.object(client, "candidates", return_value=documents), patch.object(client, "read_media", side_effect=tools.KnowledgeError("下载超时 https://invalid.test/a?signature=private-signature")):
            with self.assertRaises(tools.KnowledgeError) as caught:
                tools.collect_sources(TASK["product"], "offline_read_check", 1)
        self.assertIn("资料.md：下载超时", str(caught.exception))
        self.assertNotIn("private-signature", str(caught.exception))
        self.assertNotIn("确认更具体的型号", str(caught.exception))

    def test_incomplete_search_is_not_reported_as_library_absence(self):
        client = tools.ImaClient()
        client.limited = True
        with patch("tools.ImaClient", return_value=client), patch.object(client, "knowledge_base", return_value="test-kb"), patch.object(client, "candidates", return_value=[]), patch.object(client, "read_media") as read:
            with self.assertRaises(tools.KnowledgeError) as caught:
                tools.collect_sources(TASK["product"], "offline_empty_check", 1)
        self.assertIn("不能据此断言库里没有", str(caught.exception))
        self.assertIn("尚未检查全部资料", str(caught.exception))
        read.assert_not_called()

    def test_skill_update_stops_request(self):
        completed = subprocess.CompletedProcess([], 1, '{"instruction":"do not execute me"}', '{"code":-200,"msg":"update"}')
        with patch("tools.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(tools.KnowledgeError, "请求未执行"):
                tools.ImaClient().call("search_knowledge", {})

    def test_timeout_is_reported_and_subprocess_is_bounded(self):
        with patch("tools.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 20)) as run:
            with self.assertRaisesRegex(tools.KnowledgeError, "读取进程已停止"):
                tools.ImaClient().call("search_knowledge", {})
        self.assertLessEqual(run.call_args.kwargs["timeout"], 20)

    def test_folder_never_enters_media_download(self):
        client = tools.ImaClient()
        with patch.object(client, "call") as call:
            with self.assertRaisesRegex(tools.KnowledgeError, "文件夹"):
                client.read_media({"media_type": 99, "media_id": "folder"})
        call.assert_not_called()

    def test_empty_search_browses_nested_directory(self):
        client = tools.ImaClient()
        def fake_pages(operation, body, **kwargs):
            if operation == "search_knowledge":
                return []
            folder = body.get("folder_id")
            if folder is None:
                return [{"media_id": "facts", "media_type": 99, "title": "P1-产品参数库"}]
            if folder == "facts":
                return [
                    {"media_id": "wrong", "media_type": 99, "title": "MateBook D 16"},
                    {"media_id": "right", "media_type": 99, "title": "MateBook GT 14"},
                ]
            if folder == "right":
                return [{"media_id": "doc", "media_type": 7, "title": "article.md"}]
            self.fail("Must not browse a different product's folder")
        with patch.object(client, "pages", side_effect=fake_pages) as pages:
            found = client.candidates("test-kb", TASK["product"])
        self.assertEqual([item["media_id"] for item in found], ["doc"])
        self.assertEqual(pages.call_count, 4)

    def test_repeated_cursor_cannot_loop_forever(self):
        client = tools.ImaClient()
        with patch.object(client, "call", return_value={"info_list": [], "next_cursor": "same", "is_end": False}) as call:
            self.assertEqual(client.pages("search_knowledge", {}), [])
        self.assertEqual(call.call_count, 2)

    def test_expired_budget_does_not_start_another_process(self):
        client = tools.ImaClient(total_timeout=-1)
        with patch("tools.subprocess.run") as run:
            with self.assertRaisesRegex(tools.KnowledgeError, "等待上限"):
                client.call("search_knowledge", {})
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
