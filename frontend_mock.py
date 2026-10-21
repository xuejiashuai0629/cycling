"""Explicit UI-only fixtures. Never used as a fallback for real ima failures."""

from copy import deepcopy
import time
import uuid
import re

from agent import strategy_for, validate_task
from config import PLATFORMS
from requirements_review import REQUIRED, confirmation_text, next_state


QUESTIONS = {
    "audience": "这篇内容主要写给哪一类人？",
    "scenario": "他们具体会在什么情况下使用电脑？",
    "platform": "要发在公众号、小红书、门店朋友圈，还是门店海报？",
    "word_count": "大致需要多少字？",
    "image_count": "大概需要几张配图？",
    "price_range": "可接受的价格区间是多少？",
    "tone": "语气偏专业导购、生活化种草，还是两者平衡？",
    "goal": "希望读者看完后做什么？",
}


def chat(user_text: str, history: list, *, state: dict | None = None) -> dict:
    """Parse labelled fixture fields; never claim to understand natural language."""
    if not user_text.strip():
        return {"status": "error", "message": "【联调模拟】请填写本次回答。"}
    requirements = deepcopy(state["requirements"]) if state else {field: {"state": "missing", "value": ""} for field in (*REQUIRED, "goal")}
    labels = {"产品": "product", "受众": "audience", "场景": "scenario", "平台": "platform", "字数": "word_count", "配图": "image_count", "预算": "price_range", "语气": "tone", "目标": "goal"}
    for part in re.split(r"[；;\n]", user_text):
        match = re.match(r"\s*(产品|受众|场景|平台|字数|配图|预算|语气|目标)\s*[:：]\s*(.+)", part)
        if match:
            field, value = labels[match[1]], match[2].strip()
            if field == "platform":
                value = {"朋友圈": "门店朋友圈", "海报": "门店海报"}.get(value, value)
            requirements[field] = {"state": "clear", "value": value}
    analysis = {"requirements": requirements, "extra": "", "questions": QUESTIONS}
    data = next_state(analysis, state, PLATFORMS)
    text = confirmation_text(data["task"]) if data["stage"] == "ready_for_confirmation" else data["question"]
    return {"status": "ok", "message": "【联调模拟，按标签填写，例如 产品：MateBook GT 14；受众：老师；场景：整理课件；平台：朋友圈】\n\n" + text, "data": data}


def prepare_materials(task: dict, mode: str = "成功") -> dict:
    request_id = "mock_" + uuid.uuid4().hex
    result = {"protocol_version": "1.0", "request_id": request_id, "status": "error", "message": "", "data": {}}
    try:
        task = validate_task(task)
    except ValueError as error:
        return {**result, "message": str(error)}
    if mode == "失败":
        return {**result, "message": "【联调模拟】知识库读取失败。请重试，页面应能继续操作。"}
    if mode == "超时":
        return {**result, "message": "【联调模拟】请求超时。这是模拟错误，未发起真实网络请求。"}
    if mode == "延迟":
        time.sleep(2)
    data = {
        "task": task, "strategy": strategy_for(task), "stage": "mock",
        "sources": [{"source_id": "D1", "title": "界面测试资料（虚构）", "knowledge_base": "联调模拟，不是 ima", "text": "仅用于检查页面展示，不包含任何真实产品参数。", "match_status": "模拟数据"}],
        "images": [], "draft": None,
        "warnings": ["这是显式联调模式，结果不能用于赛事运行样例。"],
        "verification": [{"name": name, "state": "pending", "detail": "联调模拟，未执行真实核验。"} for name in ("知识库来源", "产品事实", "图片型号", "营销合规")],
    }
    return {**result, "status": "ok", "message": "【联调模拟】页面已收到测试结果。", "data": data}
