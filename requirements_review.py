"""Keep clarifying all required slots until the user can confirm the task."""

from copy import deepcopy
import re

FIELDS = ("product", "audience", "scenario", "platform", "word_count", "image_count", "price_range", "goal", "extra")
REQUIRED = FIELDS[1:7]
LABELS = {
    "product": "产品型号（可选）",
    "audience": "目标人群",
    "scenario": "使用场景",
    "platform": "发布平台",
    "word_count": "大致字数",
    "image_count": "配图数量",
    "price_range": "可接受价格区间",
    "goal": "内容目标（选填）",
    "extra": "补充要求（选填）",
}
SCENARIO_QUESTION = "具体要用电脑完成什么任务、使用哪些软件？例如学习是查文献写论文、编程作业，还是剪辑课程视频？请补充实际任务，以及会影响性能判断的情况（如多开文件、视频清晰度或项目规模）；不知道软件名可以直接描述操作。"
QUESTIONS = {
    "audience": "主要面向哪类顾客？",
    "scenario": SCENARIO_QUESTION,
    "platform": "发布在公众号、小红书、门店朋友圈，还是门店海报？",
    "word_count": "这篇推文大致需要多少字？（例如：300字、500–800字）",
    "image_count": "大概需要几张配图？（例如：2张、3–4张；最终仍需符合平台规则）",
    "price_range": "顾客可接受的价格区间大概是多少？（不知道可先写“待确认”）",
}


def broad_scenario(value: str) -> bool:
    """Catch generic labels even when a model incorrectly marks them clear.

    Detailed, freely worded tasks remain the understanding model's job. No
    task-to-performance or task-to-product mapping is inferred here.
    """
    text = re.sub(r"[\s，。！？、,./!?；;：:（）()]+", "", value).lower()
    generic = r"学习|办公|工作|娱乐|游戏|上网|上课|网课|家用|商用|内容创作|创作|设计|企业采购|采购|日常使用|使用|用途|电脑|笔记本|主要|平时|日常|平日|一般|普通|基础|基本|轻度|重度|为主|需求|需要|就|只是|是|用于|用来|拿来|用|的|在|和|与|及|并|或|也|都|兼顾|同时|学生|大学生|大学|学校|校园|宿舍|教室|图书馆|办公室|家里|居家|门店|公司|企业|里|中"
    generic = "|".join(sorted(generic.split("|"), key=len, reverse=True))
    return bool(text) and re.fullmatch(r"(?:(?>" + generic + r"))+", text) is not None


def normalize_task(task: dict) -> dict:
    if not isinstance(task, dict):
        raise ValueError("请先描述本次内容需求。")
    clean = {}
    for field in FIELDS:
        value = task.get(field, "")
        if not isinstance(value, str):
            raise ValueError(LABELS[field] + "应填写文字。")
        clean[field] = value.strip()
        if len(clean[field]) > (120 if field in ("product", "image_count") else 3000):
            raise ValueError(LABELS[field] + "过长，请精简后再提交。")
    return clean


def validate_analysis(analysis: dict, platforms: dict) -> dict:
    if not isinstance(analysis, dict) or not isinstance(analysis.get("requirements"), dict):
        raise ValueError("模型没有返回完整需求清单，请重试。")
    checklist = deepcopy(analysis["requirements"])
    task = {}
    for field in FIELDS[:-1]:
        item = checklist.get(field)
        if not isinstance(item, dict) or item.get("state") not in ("clear", "missing", "ambiguous") or not isinstance(item.get("value"), str):
            raise ValueError("模型需求清单格式异常，请重试。")
        task[field] = item["value"].strip()
        if item["state"] == "clear" and not task[field] and field != "product":
            raise ValueError("模型将空信息标记为明确，请重试。")
        if field in REQUIRED and re.sub(r"[\s，。！？,.!?]", "", task[field]) in ("随便", "都行", "都可以", "无所谓", "不知道", "不清楚", "待定", "未定", "未提供"):
            checklist[field]["state"] = "ambiguous"
    task["extra"] = analysis.get("extra", "")
    task = normalize_task(task)
    if checklist["platform"]["state"] == "clear" and task["platform"] not in platforms:
        checklist["platform"]["state"] = "ambiguous"
    broad = broad_scenario(task["scenario"])
    if broad:
        checklist["scenario"]["state"] = "ambiguous"
    unclear = [field for field in REQUIRED if checklist[field]["state"] != "clear"]
    questions = analysis.get("questions", {})
    if not isinstance(questions, dict):
        raise ValueError("模型追问格式异常，请重试。")
    lines = []
    for field in unclear:
        question = SCENARIO_QUESTION if field == "scenario" and broad else questions.get(field, QUESTIONS[field])
        if not isinstance(question, str) or not question.strip() or len(question) > 400:
            raise ValueError("模型追问内容异常，请重试。")
        lines.append(f"- **{LABELS[field]}**：{question.strip()}")
    return {"task": task, "requirements": checklist, "question_fields": unclear, "question": "还需确认以下信息，可在一条消息中一起补充：\n\n" + "\n".join(lines) if unclear else ""}


def next_state(analysis: dict, previous: dict | None, platforms: dict) -> dict:
    result = validate_analysis(analysis, platforms)
    rounds = (previous or {}).get("rounds", 0)
    if type(rounds) is not int or rounds < 0:
        raise ValueError("本次追问记录异常，请清空后重试。")
    if not result["question_fields"]:
        result.update(rounds=rounds, stage="ready_for_confirmation")
        return result
    result.update(rounds=rounds + 1, stage="clarifying")
    return result


def confirmation_text(task: dict) -> str:
    lines = ["本次需求理解如下："]
    for field in FIELDS:
        if task[field]:
            lines += [f"**{LABELS[field]}**：{task[field]}"]
    if not task["product"]:
        lines.append("**产品型号**：待资料检索 Agent 根据场景与预算从知识库中选择。")
    if not task["goal"]:
        lines.append("内容目标未指定，将按平台常规结构组织，不补写价格、优惠或服务承诺。")
    lines.append("请确认上述理解是否准确；也可以直接提出修改。确认后再查资料。")
    return "\n\n".join(lines)
