from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import agent
from config import ROOT
import llm
from requirements_review import next_state
from config import PLATFORMS
from tests.fixtures import TASK, DRAFT, REVIEW, analysis, unclear, prepared
from tools import KnowledgeError
from tests.test_image_assets import PNG


class UiTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch("llm.is_ready", return_value=True), patch("llm.understand", side_effect=AssertionError("Unexpected model call"))):
            mock.start()
            self.addCleanup(mock.stop)

    def check(self, app):
        self.assertFalse(app.exception, [item.message for item in app.exception])
        return app

    def make_app(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()
        app.radio(key="run_mode").set_value("真实资料").run()
        return self.check(app)

    def button(self, app, label):
        return next(button for button in app.button if button.label == label)

    def send(self, app, text, result=None):
        with patch("llm.understand", return_value=result or analysis()):
            app.chat_input[0].set_value(text).run()
        return self.check(app)

    def ready(self):
        app = self.make_app()
        self.send(app, "给乡村支教老师介绍 MateBook GT 14，整理课件，发朋友圈，邀请咨询，不写价格")
        self.assertEqual(app.session_state["dialog"]["stage"], "ready_for_confirmation")
        return app

    def prepare(self, app):
        with patch("agent.prepare_materials", return_value=prepared()) as real:
            self.button(app, "确认需求，查找资料与图片").click().run()
        self.check(app)
        self.assertFalse(app.session_state["busy"])
        return real

    def test_conversation_success_rerun_and_clear(self):
        app = self.ready()
        self.prepare(app)
        self.check(app)
        count = len(app.session_state["history"])
        self.assertEqual(count, 3)
        self.assertEqual(len(app.tabs), 4)
        self.assertEqual(app.session_state["result"]["data"]["stage"], "materials_ready")
        app.run()
        self.assertEqual(len(app.session_state["history"]), count)
        self.button(app, "清空任务与记录").click().run()
        self.assertEqual(app.session_state["history"], [])
        self.assertIsNone(app.session_state["dialog"])
        self.assertIsNone(app.session_state["result"])
        self.assertIsNone(app.session_state["confirmed_task"])
        self.assertFalse(app.session_state["busy"])
        self.check(app)

    def test_unrestricted_scenario_followup_and_confirmation_before_search(self):
        app = self.make_app()
        with patch("agent.prepare_materials") as search:
            self.send(app, "帮老师写一篇", unclear())
            self.assertEqual(app.session_state["dialog"]["question_fields"], ["scenario"])
            self.assertFalse(any(button.label == "确认需求，查找资料与图片" for button in app.button))
            self.send(app, TASK["scenario"])
            search.assert_not_called()
        search = self.prepare(app)
        self.assertEqual(search.call_args.args[0], TASK)
        self.assertEqual(app.session_state["confirmed_task"], TASK)

    def test_broad_study_answer_requires_tasks_before_confirmation_or_search(self):
        app = self.make_app()
        broad = analysis()
        broad["requirements"]["scenario"]["value"] = "学习"
        with patch("agent.prepare_materials") as search, patch("llm.generate") as write:
            self.send(app, "给大学生写，使用场景是学习", broad)
            self.assertEqual(app.session_state["dialog"]["stage"], "clarifying")
            self.assertIn("具体要用电脑完成什么任务", app.session_state["history"][-1]["content"])
            self.assertFalse(any(button.label == "确认需求，查找资料与图片" for button in app.button))
            search.assert_not_called()
            write.assert_not_called()
        self.send(app, TASK["scenario"])
        self.assertEqual(app.session_state["dialog"]["stage"], "ready_for_confirmation")

    def test_material_failure_keeps_prior_result_and_recovers(self):
        app = self.ready()
        self.prepare(app)
        previous = deepcopy(app.session_state["result"])
        count = len(app.session_state["history"])
        with patch("agent.prepare_materials", return_value={"status": "error", "message": "资料读取失败"}):
            self.button(app, "确认需求，查找资料与图片").click().run()
        self.check(app)
        self.assertEqual(app.session_state["result"], previous)
        self.assertEqual(len(app.session_state["history"]), count)
        self.assertIn("失败", app.error[0].value)
        self.prepare(app)
        self.assertFalse(app.error)
        self.assertEqual(len(app.session_state["history"]), count + 1)

    def test_timeout_allows_retry(self):
        app = self.ready()
        count = len(app.session_state["history"])
        with patch("agent.prepare_materials", return_value={"status": "error", "message": "资料读取超时"}):
            self.button(app, "确认需求，查找资料与图片").click().run()
        self.check(app)
        self.assertIn("超时", app.error[0].value)
        self.assertEqual(len(app.session_state["history"]), count)
        self.prepare(app)
        self.assertFalse(app.error)

    def test_real_failure_never_falls_back_to_mock(self):
        app = self.ready()
        count = len(app.session_state["history"])
        with patch("agent.prepare_materials", return_value={"status": "error", "message": "真实读取测试失败"}) as real, patch("frontend_mock.prepare_materials") as mock:
            self.button(app, "确认需求，查找资料与图片").click().run()
        real.assert_called_once()
        mock.assert_not_called()
        self.assertEqual(len(app.session_state["history"]), count)
        self.assertFalse(app.session_state["busy"])

    def test_quota_reason_is_visible_and_chat_remains_usable(self):
        app = self.ready()
        previous_history = deepcopy(app.session_state["history"])
        with patch("agent.runtime_issues", return_value=[]), patch("agent.collect_sources", side_effect=KnowledgeError("资料获取次数已达上限，请明天再尝试")) as collect:
            self.button(app, "确认需求，查找资料与图片").click().run()
        self.check(app)
        collect.assert_called_once()
        self.assertIn("ima 原文读取额度已用尽", app.error[0].value)
        self.assertIn("资料获取次数已达上限，请明天再尝试", app.error[0].value)
        self.assertEqual(app.session_state["history"], previous_history)
        self.assertFalse(app.session_state["busy"])
        self.assertFalse(app.chat_input[0].disabled)
        self.check(app)

    def test_local_mode_is_default_and_loads_without_any_ima_request(self):
        with patch("tools.ImaClient", side_effect=AssertionError("Local mode must not use ima")):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()
            self.check(app)
            self.assertEqual(app.session_state["run_mode"], "本地资料测试")
            self.assertEqual(app.radio(key="run_mode").options, ["本地资料测试", "真实资料"])
            self.assertFalse(app.selectbox)
            self.assertTrue(self.button(app, "检查知识库连接").disabled)
            self.assertTrue(any("当前为本地资料测试，未调用 ima" in item.value for item in app.warning))
            self.send(app, "完整需求")
            value = prepared()["data"]
            materials = {key: value[key] for key in ("sources", "images", "warnings")}
            with patch("local_materials.collect_local_sources", return_value=materials) as local:
                self.button(app, "确认需求，查找资料与图片").click().run()
            self.check(app)
            local.assert_called_once()
            self.assertEqual(app.session_state["result"]["data"]["material_mode"], "local")
            self.assertEqual(app.session_state["result"]["data"]["verification"][0]["state"], "pending")
            self.assertTrue(any("当前结果来自本地文件测试" in item.value for item in app.warning))
            app.radio(key="run_mode").set_value("真实资料").run()
            self.assertIsNone(app.session_state["result"])
            self.assertIsNone(app.session_state["confirmed_task"])
            self.assertFalse(self.button(app, "检查知识库连接").disabled)

    def test_busy_disables_input_confirm_clear_and_mode_switch(self):
        app = self.ready()
        app.session_state["busy"] = True
        app.run()
        self.assertTrue(app.chat_input[0].disabled)
        self.assertTrue(all(button.disabled for button in app.button))
        self.assertTrue(app.radio(key="run_mode").disabled)
        self.check(app)

    def test_previously_generated_draft_loses_image_and_is_no_longer_delivered(self):
        app = self.ready()
        self.prepare(app)
        result = deepcopy(app.session_state["result"])
        result["data"].update(draft=deepcopy(DRAFT), stage="pending_verification")
        result["data"]["images"][0]["path"] = "outputs/absent-after-generation.png"
        app.session_state["result"] = result
        app.run()
        self.check(app)
        self.assertTrue(any("配图检查未通过" in message.value for message in app.error))
        self.assertFalse(app.tabs[2].subheader)
        self.assertFalse(app.tabs[2].code)
        self.assertFalse(any(item.label == "下载图文与原图（ZIP）" for item in app.get("download_button")))
        self.assertTrue(self.button(app, "生成文案并审核").disabled)
        self.assertNotIn(DRAFT["title"], agent.export_markdown(result))

    def test_model_error_excluded_from_history_and_unexpected_error_recovers(self):
        app = self.make_app()
        for error in (llm.ModelError("模型等待超时"), RuntimeError("private-detail")):
            with patch("llm.understand", side_effect=error):
                app.chat_input[0].set_value("写文案").run()
            self.check(app)
            self.assertFalse(app.session_state["busy"])
            self.assertEqual(app.session_state["history"], [])
            self.assertNotIn("private-detail", app.error[0].value)
        self.send(app, "完整需求")
        self.assertEqual(len(app.session_state["history"]), 2)

    def test_unresolved_answers_keep_input_open_without_starting_work(self):
        app = self.make_app()
        for index in range(8):
            self.send(app, "还不清楚", unclear())
        self.assertEqual(app.session_state["dialog"]["stage"], "clarifying")
        self.assertFalse(app.chat_input[0].disabled)
        self.assertEqual(app.session_state["dialog"]["rounds"], 8)
        self.assertFalse(any(button.label == "确认需求，查找资料与图片" for button in app.button))
        self.assertIsNone(app.session_state["result"])
        self.send(app, "补齐了")
        self.assertEqual(app.session_state["dialog"]["stage"], "ready_for_confirmation")

    def test_changed_requirements_invalidate_old_materials(self):
        app = self.ready()
        self.prepare(app)
        changed = analysis()
        changed["requirements"]["scenario"]["value"] = "新场景：为音乐课准备示范音频"
        self.send(app, "改写给音乐老师", changed)
        self.assertIsNone(app.session_state["confirmed_task"])
        self.assertTrue(self.button(app, "生成文案并审核").disabled)

    def test_chained_edits_always_require_confirmation_and_new_review(self):
        app = self.ready()
        self.prepare(app)
        task = deepcopy(TASK)
        for field, value in (("extra", "删去续航内容"), ("platform", "门店朋友圈"), ("scenario", "企业采购，用Excel汇总多门店报表并同时开视频会议"), ("product", "MateBook D 14")):
            task[field] = value
            understanding = analysis()
            for key in understanding["requirements"]:
                understanding["requirements"][key]["value"] = task[key]
            understanding["extra"] = task["extra"]
            with patch("agent.prepare_materials") as collect, patch("llm.generate") as write:
                self.send(app, "修改" + field + "：" + value, understanding)
            collect.assert_not_called()
            write.assert_not_called()
            self.assertIsNone(app.session_state["confirmed_task"])
            self.assertTrue(self.button(app, "生成文案并审核").disabled)
            new_materials = prepared()
            new_materials["data"].update(task=deepcopy(task), strategy=agent.strategy_for(task))
            with patch("agent.prepare_materials", return_value=new_materials) as collect:
                self.button(app, "确认需求，查找资料与图片").click().run()
            self.assertEqual(collect.call_args.args[0], task)
            with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", return_value=REVIEW) as review:
                self.button(app, "生成文案并审核").click().run()
            self.assertEqual(write.call_args.args[0], task)
            review.assert_called_once()
            self.check(app)

    def test_connection_check_shows_summary_without_changing_chat(self):
        app = self.ready()
        history = deepcopy(app.session_state["history"])
        response = {"status": "ok", "message": "本次实时验证成功", "checked_at": "2026-10-18T08:00:00Z", "response_summary": {"业务码": 0, "本页条目数": 2}, "scope": "未逐份核对原文"}
        with patch("diagnostics.check_ima_connection", return_value=response) as check:
            self.button(app, "检查知识库连接").click().run()
        check.assert_called_once()
        self.assertEqual(app.session_state["history"], history)
        self.assertEqual(app.session_state["connection_status"], response)
        self.assertFalse(app.session_state["busy"])
        self.check(app)

    def test_generated_draft_and_blocked_review_are_displayed_correctly(self):
        app = self.ready()
        self.prepare(app)
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=REVIEW):
            self.button(app, "生成文案并审核").click().run()
        self.check(app)
        self.assertEqual(app.session_state["result"]["data"]["draft"], DRAFT)
        blocked = deepcopy(REVIEW) | {"decision": "blocked", "issues": ["资料有冲突"]}
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=blocked):
            self.button(app, "生成文案并审核").click().run()
        self.check(app)
        self.assertIsNone(app.session_state["result"]["data"]["draft"])
        self.assertEqual(app.session_state["result"]["data"]["stage"], "blocked")
        self.assertFalse(any(item.value == DRAFT["title"] for item in app.subheader))

    def test_actual_image_is_shown_in_article_and_download_includes_original(self):
        with TemporaryDirectory(prefix="ui_image_", dir=ROOT / "outputs") as directory:
            path = Path(directory) / "actual.png"
            path.write_bytes(PNG)
            app = self.ready()
            result = prepared()
            result["data"].update(draft=deepcopy(DRAFT), stage="pending_verification", material_mode="local")
            result["data"]["images"][0]["path"] = path.relative_to(ROOT).as_posix()
            app.session_state["result"] = result
            app.session_state["confirmed_task"] = deepcopy(TASK)
            app.run()
            self.check(app)
            self.assertEqual(len(app.tabs[2].get("image")), 1)
            self.assertEqual(len(app.tabs[1].get("image")), 1)
            self.assertTrue(any(item.label == "下载图文与原图（ZIP）" for item in app.get("download_button")))

    def test_auto_rewrites_past_two_without_extra_clicks_or_history_noise(self):
        app = self.ready()
        self.prepare(app)
        history_count = len(app.session_state["history"])
        failing = deepcopy(REVIEW)
        failing["checks"][3].update(state="fail", detail="正文像参数卡，需改成场景短文。")
        failing["revision_requests"] = ["改为自然短段落。"]
        with patch("agent.collect_sources") as collect, patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", side_effect=[failing] * 4 + [REVIEW]) as review:
            self.button(app, "生成文案并审核").click().run()
        self.check(app)
        collect.assert_not_called()
        self.assertEqual(write.call_count, 5)
        self.assertEqual(review.call_count, 5)
        self.assertEqual(app.session_state["result"]["data"]["rewrite_count"], 4)
        self.assertEqual(app.session_state["result"]["data"]["draft"], DRAFT)
        self.assertEqual(len(app.session_state["history"]), history_count + 1)
        self.assertFalse(app.session_state["busy"])
        self.assertNotIn("pending_action", app.session_state)

    def test_network_pause_can_resume_review_without_new_draft(self):
        app = self.ready()
        self.prepare(app)
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", side_effect=llm.ModelError("模型等待超时")):
            self.button(app, "生成文案并审核").click().run()
        self.check(app)
        self.assertEqual(app.session_state["result"]["data"]["stage"], "generation_paused")
        self.assertIsNone(app.session_state["result"]["data"]["draft"])
        self.assertFalse(app.session_state["busy"])
        with patch("llm.generate") as write, patch("llm.review", return_value=REVIEW):
            self.button(app, "继续写作与审核").click().run()
        write.assert_not_called()
        self.assertEqual(app.session_state["result"]["data"]["draft"], DRAFT)
        self.assertFalse(app.error)
        self.check(app)

    def test_pause_button_stops_job_and_resume_keeps_specific_feedback(self):
        app = self.ready()
        self.prepare(app)
        failing = deepcopy(REVIEW) | {"revision_requests": ["重写开头的场景描写。"]}
        with patch("llm.generate", return_value=DRAFT), patch("llm.review", return_value=failing):
            first = agent.generate_content(prepared())
        app.session_state["result"] = first
        app.run()
        with patch("llm.generate") as write, patch("llm.review") as review:
            self.button(app, "暂停自动重写").click().run()
        write.assert_not_called()
        review.assert_not_called()
        self.assertEqual(app.session_state["result"]["data"]["stage"], "generation_paused")
        self.assertFalse(app.session_state["busy"])
        self.assertNotIn("pending_action", app.session_state)
        with patch("llm.generate", return_value=DRAFT) as write, patch("llm.review", return_value=REVIEW):
            self.button(app, "继续写作与审核").click().run()
        self.assertIn("重写开头的场景描写。", write.call_args.kwargs["feedback"])
        self.assertEqual(app.session_state["result"]["data"]["rewrite_count"], 1)
        self.check(app)


if __name__ == "__main__":
    unittest.main()
