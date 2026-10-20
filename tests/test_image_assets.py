from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

import agent
from config import OUTPUTS, ROOT
from image_assets import bundle, delivery_issues, image_file
from tests.fixtures import DRAFT, prepared, sample_png


PNG = sample_png()


class ImageAssetsTests(unittest.TestCase):
    def setUp(self):
        OUTPUTS.mkdir(exist_ok=True)
        self.directory = TemporaryDirectory(prefix="image_test_", dir=OUTPUTS)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "实际图片.png"
        self.path.write_bytes(PNG)
        self.result = prepared()
        self.data = self.result["data"]
        self.data["images"][0]["path"] = self.path.relative_to(ROOT).as_posix()
        self.data.update(draft=deepcopy(DRAFT), material_mode="local", stage="pending_verification")

    def test_body_has_actual_image_between_corresponding_paragraphs(self):
        self.data["draft"]["body"] = "第一段\n\n【配图 I1】\n\n第二段"
        sections = agent.body_sections(self.data)
        self.assertEqual([part["kind"] for part in sections], ["text", "image", "text"])
        self.assertIn("第一段", sections[0]["text"])
        self.assertIn("第二段", sections[2]["text"])
        self.assertEqual(image_file(sections[1]["picture"]), self.path)
        markdown = agent.draft_markdown(self.data)
        self.assertIn("![离线测试图.png]", markdown)
        self.assertLess(markdown.index("第一段"), markdown.index("!["))
        self.assertLess(markdown.index("!["), markdown.index("第二段"))
        self.assertIn("型号待核验", markdown)

    def test_zip_contains_real_bytes_and_portable_markdown_without_private_ids(self):
        with ZipFile(BytesIO(bundle(self.result))) as archive:
            self.assertEqual(set(archive.namelist()), {"营销创作记录.md", "images/image_1.png"})
            self.assertEqual(archive.read("images/image_1.png"), PNG)
            text = archive.read("营销创作记录.md").decode("utf-8")
        self.assertIn("![离线测试图.png](<images/image_1.png>)", text)
        self.assertIn("当前为本地资料测试，未调用 ima", text)
        self.assertNotIn("private-id", text)
        self.assertNotIn("private-url", text)
        for part in ("一、场景判断", "二、知识库依据", "三、图文软文", "四、核验结果"):
            self.assertIn(part, text)

    def test_missing_untrusted_or_invalid_images_do_not_become_placeholder_packages(self):
        invalid = Path(self.directory.name) / "invalid.png"
        invalid.write_text("not an image", encoding="utf-8")
        for value in ("outputs/missing.png", "../README.md", "https://invalid.test/image.png", invalid.relative_to(ROOT).as_posix()):
            with self.subTest(value=value):
                self.data["images"][0]["path"] = value
                with self.assertRaises((ValueError, OSError)):
                    bundle(self.result)
                self.assertEqual(agent.draft_markdown(self.data), "")
                exported = agent.export_markdown(self.result)
                self.assertNotIn(DRAFT["title"], exported)
                self.assertIn("配图检查未通过", exported)

    def test_spoofed_header_and_truncated_pixels_cannot_be_delivered(self):
        for content in (b"\x89PNG\r\n\x1a\nnot-a-real-image", PNG[:50]):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(ValueError):
                    image_file(self.data["images"][0])
                self.assertEqual(agent.draft_markdown(self.data), "")
                with self.assertRaises(ValueError):
                    bundle(self.result)

    def test_all_platforms_require_real_distinct_files_at_minimum_image_count(self):
        for platform, count in (("公众号", 2), ("小红书", 3), ("门店朋友圈", 1), ("门店海报", 1)):
            with self.subTest(platform=platform):
                self.data["task"]["platform"] = platform
                self.data["strategy"] = agent.strategy_for(self.data["task"])
                pictures, plans = [], []
                for index in range(1, count + 1):
                    path = Path(self.directory.name) / f"actual_{index}.png"
                    path.write_bytes(sample_png(index))
                    pictures.append(self.data["images"][0] | {"source_id": f"I{index}", "path": path.relative_to(ROOT).as_posix()})
                    plans.append(DRAFT["image_plan"][0] | {"source_id": f"I{index}"})
                self.data["images"] = pictures
                self.data["draft"]["image_plan"] = plans
                self.data["draft"]["body"] = DRAFT["claims"][0]["text"] + "\n\n" + "\n\n".join(f"【配图 I{index}】" for index in range(1, count + 1))
                self.assertEqual(delivery_issues(self.data), [])
                with ZipFile(BytesIO(bundle(self.result))) as archive:
                    self.assertEqual(len(archive.namelist()), count + 1)
                (ROOT / pictures[-1]["path"]).unlink()
                self.assertTrue(delivery_issues(self.data))
                self.assertEqual(agent.draft_markdown(self.data), "")
                with self.assertRaises(ValueError):
                    bundle(self.result)

    def test_unknown_ids_duplicates_and_mismatched_markers_block_export(self):
        for plan, body in (([DRAFT["image_plan"][0] | {"source_id": "I99"}], "【配图 I99】"),
                           (DRAFT["image_plan"] * 2, DRAFT["body"]),
                           (DRAFT["image_plan"], "没有插图标记的正文")):
            with self.subTest(plan=plan, body=body):
                self.data["draft"].update(image_plan=plan, body=body)
                self.assertTrue(delivery_issues(self.data))
                with self.assertRaises(ValueError):
                    bundle(self.result)

    def test_same_bytes_under_different_names_do_not_count_as_two_images(self):
        copy_path = Path(self.directory.name) / "copy.png"
        copy_path.write_bytes(PNG)
        self.data["task"]["platform"] = "公众号"
        self.data["strategy"] = agent.strategy_for(self.data["task"])
        self.data["images"].append(self.data["images"][0] | {"source_id": "I2", "path": copy_path.relative_to(ROOT).as_posix()})
        self.data["draft"]["image_plan"].append(DRAFT["image_plan"][0] | {"source_id": "I2"})
        self.data["draft"]["body"] += "\n\n【配图 I2】"
        self.assertTrue(any("重复凑图数" in issue for issue in delivery_issues(self.data)))
        with self.assertRaises(ValueError):
            bundle(self.result)

    def test_only_selected_images_are_packed_and_blocked_drafts_cannot_download(self):
        self.data["images"].append(self.data["images"][0] | {"source_id": "I2", "path": "outputs/missing.png"})
        with ZipFile(BytesIO(bundle(self.result))) as archive:
            self.assertEqual(len(archive.namelist()), 2)
        self.data.update(draft=None, stage="blocked")
        with self.assertRaises(ValueError):
            bundle(self.result)


if __name__ == "__main__":
    unittest.main()
