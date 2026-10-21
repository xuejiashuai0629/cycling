"""Invented offline fixtures; never presented as a real model/ima run."""

from copy import deepcopy
from io import BytesIO

from PIL import Image

import frontend_mock
from config import OUTPUTS, ROOT


def sample_png(index=1):
    stream = BytesIO()
    Image.new("RGB", (8, 8), (index % 256, 50, 100)).save(stream, format="PNG")
    return stream.getvalue()


def test_image_path(index=1):
    directory = OUTPUTS / "test"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"offline_fixture_{index}.png"
    path.write_bytes(sample_png(index))
    return path.relative_to(ROOT).as_posix()

TASK = {
    "product": "MateBook GT 14", "audience": "乡村支教老师",
    "scenario": "平日在学校整理课件，周末到镇上同步资料",
    "platform": "门店朋友圈", "word_count": "按平台常规",
    "image_count": "1 张", "price_range": "4000–6000 元",
    "tone": "专业导购",
    "goal": "邀请咨询适合备课的配置", "extra": "不写价格",
}


def analysis(**overrides):
    result = {"requirements": {key: {"state": "clear", "value": TASK[key]} for key in TASK if key != "extra"}, "extra": TASK["extra"], "questions": {}}
    result.update(overrides)
    return result


def unclear(field="scenario"):
    result = analysis()
    result["requirements"][field] = {"state": "ambiguous", "value": "待定"}
    result["questions"][field] = "可以描述一次具体的使用情境吗？"
    return result


def prepared():
    result = frontend_mock.prepare_materials(TASK)
    result["data"].update(stage="materials_ready", warnings=[], sources=[{"source_id": "D1", "title": "离线测试原文.md", "knowledge_base": "测试库", "text": "离线测试事实：提供资料整理功能。仅为测试，不是真实产品参数。", "media_id": "private-id"}], images=[{"source_id": "I1", "title": "离线测试图.png", "path": test_image_path(), "knowledge_base": "测试库", "position": "待安排", "description": "候选图", "url": "https://invalid.test/private-url"}])
    return result


DRAFT = {"angle": "围绕老师备课整理课件的需求切入", "title": "为备课安排一套资料整理流程", "intro": "从日常备课出发。", "body": "提供资料整理功能。\n\n【配图 I1】", "cta": "欢迎咨询。", "selling_points": ["提供资料整理功能。"], "tags": ["备课"], "claims": [{"text": "提供资料整理功能。", "source_id": "D1", "quote": "提供资料整理功能。"}], "image_plan": [{"source_id": "I1", "position": "正文后", "description": "候选图，型号待核验"}]}
REVIEW = {"decision": "revise", "checks": [{"name": name, "state": "pass", "detail": "离线审核测试"} for name in ("产品事实", "图片型号", "平台图数", "平台格式", "场景匹配", "营销合规")], "issues": ["图片尚待核验。"], "revision_requests": [], "pending_items": ["图片尚待核验。"]}


def copy_draft():
    return deepcopy(DRAFT)
