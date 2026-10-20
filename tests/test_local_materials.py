from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
from config import ROOT
import local_materials
from tests.fixtures import DRAFT, REVIEW, TASK, prepared
from tools import KnowledgeError


class LocalMaterialsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "outputs").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "outputs")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.library = self.directory / "library"
        self.library.mkdir()
        for change in (
            patch("local_materials.LOCAL_DIRECTORY", self.library),
            patch("local_materials.OUTPUTS", self.directory / "downloaded"),
            patch("tools.ImaClient", side_effect=AssertionError("No ima request allowed")),
            patch("agent.collect_sources", side_effect=AssertionError("No remote fallback allowed")),
        ):
            change.start()
            self.addCleanup(change.stop)

    def add_text(self, name, text="测试原文，不代表真实参数。"):
        path = self.library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_local_files_match_model_and_keep_traceable_notice(self):
        self.add_text("MateBook GT 14.md")
        self.add_text("MateBook D 14.md", "错误型号不应读取")
        self.add_text("MateBook GT 140.md", "错误型号不应读取")
        folder = self.library / "MateBook GT 14"
        folder.mkdir()
        self.add_text("MateBook GT 14/参数.md")
        (folder / "主视觉.png").write_bytes(b"\x89PNG\r\n\x1a\nlocal-test-signature-only")
        data = local_materials.collect_local_sources("matebook gt 14", "offline_read", 1)
        self.assertEqual(len(data["sources"]), 2)
        self.assertEqual(len(data["images"]), 1)
        self.assertEqual(data["material_mode"], "local")
        self.assertIn("未调用 ima", data["source_notice"])
        self.assertTrue((ROOT / data["images"][0]["path"]).is_file())
        self.assertTrue(all("本地" in item["knowledge_base"] for item in data["sources"] + data["images"]))
        self.assertTrue(all("local_path" in item for item in data["sources"] + data["images"]))
        self.assertTrue(all("错误型号" not in item["text"] for item in data["sources"]))

    def test_missing_local_material_is_explicit_without_remote_fallback(self):
        with self.assertRaisesRegex(KnowledgeError, "未调用 ima.*本地目录未找到"):
            local_materials.collect_local_sources("MateBook GT 14", "offline_missing", 1)

    def test_invalid_local_text_is_reported(self):
        (self.library / "MateBook GT 14.md").write_bytes(b"\xff\xfeinvalid")
        with self.assertRaisesRegex(KnowledgeError, "不是 UTF-8"):
            local_materials.collect_local_sources("MateBook GT 14", "offline_bad_text", 1)

    def test_request_cannot_escape_project_output_directory(self):
        with self.assertRaisesRegex(KnowledgeError, "任务编号"):
            local_materials.collect_local_sources("MateBook GT 14", "../escape", 1)

    def test_prepare_marks_local_sources_without_claiming_remote_verification(self):
        self.add_text("MateBook GT 14.md")
        with patch("agent.runtime_issues", return_value=[]):
            result = agent.prepare_materials(TASK, mode="local")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["material_mode"], "local")
        self.assertEqual(result["data"]["verification"][0]["state"], "pending")
        self.assertIn("未调用 ima", result["message"])
        exported = agent.export_markdown(result)
        self.assertIn("当前为本地资料测试", exported)
        self.assertIn("本地测试文件：", exported)
        self.assertNotIn("已通过官方接口", exported)

    def test_local_notice_survives_writing_review_copy_and_export(self):
        value = prepared()
        value["data"]["material_mode"] = "local"
        with patch("llm.generate", return_value=deepcopy(DRAFT)), patch("llm.review", return_value=deepcopy(REVIEW)):
            result = agent.generate_content(value)
        self.assertIsNotNone(result["data"]["draft"])
        self.assertEqual(result["data"]["verification"][0]["state"], "pending")
        self.assertNotIn("已通过官方接口", str(result["data"]["verification"]))
        self.assertIn("当前为本地资料测试，未调用 ima", agent.draft_markdown(result["data"]))
        self.assertIn("当前为本地资料测试，未调用 ima", agent.export_markdown(result))
