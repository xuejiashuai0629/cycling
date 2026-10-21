"""Scene and platform orchestration. All preparation results remain unverified."""

from copy import deepcopy
from datetime import datetime, timezone
import uuid
import re

from config import PLATFORMS, redact, runtime_issues
import llm
from requirements_review import SCENARIO_QUESTION, broad_scenario, confirmation_text, next_state, normalize_task
from content_review import CHECKS, platform_format_issues, validate_draft, validate_review
from tools import KnowledgeError, collect_sources, normalized
from safety_review import draft_issues, evidence_issues
from image_assets import delivery_issues, usable_images


def validate_task(task: dict) -> dict:
    clean = normalize_task(task)
    for key, label in (("audience", "目标人群"), ("scenario", "使用场景"), ("platform", "发布平台")):
        if not clean[key]:
            raise ValueError("请先补充" + label + "。")
    clean["word_count"] = clean["word_count"] or "按平台常规"
    clean["image_count"] = clean["image_count"] or "按平台规则"
    clean["price_range"] = clean["price_range"] or "待确认"
    if clean["platform"] not in PLATFORMS:
        raise ValueError("请先选择支持的发布平台。")
    if broad_scenario(clean["scenario"]):
        raise ValueError("使用场景还不具体，不能仅凭“学习”“办公”等标签判断电脑需求。" + SCENARIO_QUESTION)
    return clean


def strategy_for(task: dict) -> dict:
    rules = PLATFORMS[task["platform"]]
    budget = task.get("price_range") or "待确认"
    target_length = task.get("word_count") or "按平台常规"
    target_images = task.get("image_count") or "按平台规则"
    return {
        **deepcopy(rules),
        "focus": f"围绕你描述的场景：{task['scenario']}\n面向：{task['audience']}。大致字数：{target_length}；配图数量：{target_images}；可接受价格区间：{budget}。具体问题、卖点和表达顺序待需求理解模型结合资料判断。",
        "goal": task["goal"] or "未指定，按平台常规结构组织，不增加未经确认的承诺",
    }


def source_verification(data: dict) -> dict:
    if data.get("material_mode") == "local":
        return {"name": "知识库来源", "state": "pending", "detail": "当前为本地资料测试，未调用 ima 知识库；仅记录本地文件来源。"}
    return {"name": "知识库来源", "state": "pass", "detail": "已通过官方接口读取指定共享知识库原文；来源可追溯。"}


def prepare_materials(task: dict, *, mode: str = "ima") -> dict:
    request_id = uuid.uuid4().hex
    envelope = {"protocol_version": "1.0", "request_id": request_id, "status": "error", "message": "", "data": {}}
    try:
        task = validate_task(task)
        if mode not in ("ima", "local"):
            raise ValueError("资料模式无效，请明确选择真实知识库或本地资料测试。")
        issues = runtime_issues()
        if issues:
            raise ValueError(" ".join(issues))
        if not task["product"]:
            from local_materials import product_candidates
            candidates = product_candidates()
            if not candidates:
                raise KnowledgeError("本地选型目录不可用，请检查项目资料目录后重试。")
            task["product"] = llm.select_product(task, candidates)
        strategy = strategy_for(task)
        if mode == "local":
            from local_materials import collect_local_sources
            materials = collect_local_sources(task["product"], request_id, strategy["images"][0])
        else:
            materials = collect_sources(task["product"], request_id, strategy["images"][0])
        data = {
            "task": task, "strategy": strategy, **materials, "draft": None,
            "material_mode": mode,
            "stage": "materials_ready", "created_at": datetime.now(timezone.utc).isoformat(),
            "verification": [
                source_verification({"material_mode": mode}),
                {"name": "产品事实", "state": "pending", "detail": "尚未逐项提取、核对事实及资料冲突。"},
                {"name": "图片型号", "state": "pending", "detail": "候选图片仅做名称筛选；具体型号、版本和图像内容尚未核对。"},
                {"name": "营销合规", "state": "pending", "detail": "尚未生成文案，未执行完整合规核验。"},
            ],
        }
        selected_note = "系统已根据场景与预算从知识库索引中选择型号：" + task["product"] + "。"
        message = selected_note + ("当前为本地资料测试，未调用 ima 知识库。已读取本地文件，文案与核验待完成。" if mode == "local" else "已读取真实资料，文案与核验待完成。")
        envelope.update(status="ok", message=message, data=data)
    except (ValueError, KnowledgeError, OSError) as error:
        message = str(error) if not isinstance(error, OSError) else "本地资料读取失败，请检查项目目录权限。"
        if isinstance(error, KnowledgeError) and error.quota_exhausted:
            message = "ima 原文读取额度已用尽。接口返回：" + message + "。本次已停止继续读取，请按 ima 提示明天再试。"
        envelope["message"] = redact(message)
    if isinstance(task, dict) and task.get("product"):
        from diagnostics import save_materials_summary
        save_materials_summary(envelope, task["product"], mode=mode)
    return envelope


def generate_content(prepared: dict, *, resume: bool = False) -> dict:
    """Advance one write/review round; the UI continues unfinished jobs."""
    result = _generate_content(prepared, resume=resume)
    # Persist only the verification summary, never a blocked body, media IDs,
    # signed download URLs or headers. This makes failures diagnosable later.
    if result.get("data", {}).get("verification"):
        from diagnostics import save_review_summary
        save_review_summary(result)
    return result


def failure_reasons(data: dict) -> list[str]:
    reasons = [item["name"] + "：" + item["detail"] for item in data.get("verification", []) if item["state"] == "fail"]
    return list(dict.fromkeys(reasons or data.get("review_issues", [])))


def blocked_message(data: dict) -> str:
    reasons = failure_reasons(data)
    return "本次暂不交付正文。具体原因：" + "；".join(reasons[:3]) if reasons else "本次审核未完成，请查看核验结果中的待确认事项。"


def pause_generation(result: dict) -> dict:
    result = deepcopy(result)
    if result.get("data", {}).get("_rewrite"):
        result["data"].update(draft=None, stage="generation_paused")
        result.update(status="ok", message="已暂停自动修改，可继续本次写作与审核。")
    return result


def _await_images(result: dict, details: list[str]) -> dict:
    data = result["data"]
    detail = "；".join(details)
    checks = [check for check in data.get("verification", []) if check["name"] != "真实配图"]
    data.update(draft=None, stage="awaiting_materials", review_issues=details,
                verification=checks + [{"name": "真实配图", "state": "fail", "detail": detail}])
    data.pop("_rewrite", None)
    return {**result, "status": "error", "message": detail}


def _generate_content(prepared: dict, *, resume: bool = False) -> dict:
    result = deepcopy(prepared)
    if not result.get("data", {}).get("sources"):
        return {**result, "status": "error", "message": "请先成功读取本次任务的资料。"}
    data = result["data"]
    try:
        if data.get("stage") == "mock":
            raise ValueError("联调模拟素材不能用于真实文案生成，请切回真实资料模式。")
        job = data.get("_rewrite") if resume else None
        if job and job["task"] != data["task"]:
            data.pop("_rewrite", None)
            data.update(draft=None, stage="blocked")
            return {**result, "status": "error", "message": "需求已变化，请重新确认并读取资料后再生成。"}
        if not job:
            data.update(draft=None, stage="review_required", review_issues=[], review_history=[], rewrite_count=0)
            job = {"task": deepcopy(data["task"]), "attempt": 0, "phase": "write", "previous_draft": None, "feedback": [], "candidate": None}
            data["_rewrite"] = job
        data["draft"] = None
        conflicts = evidence_issues(data)
        if conflicts:
            data.update(stage="blocked", review_issues=conflicts, verification=[{"name": "产品事实", "state": "fail", "detail": "；".join(conflicts)}])
            data.pop("_rewrite", None)
            return {**result, "status": "error", "message": "发现资料型号不符或参数冲突，已停止成稿，请人工确认资料。"}
        minimum, maximum = data["strategy"]["images"]
        usable, image_issues = usable_images(data)
        if len(usable) < minimum:
            detail = f"只有{len(usable)}张可完整读取且未被标题排除的不同图片，{data['task']['platform']}要求{minimum}–{maximum}张；需补充对应型号图片后继续，不能靠重写补图。"
            return _await_images(result, [detail] + image_issues)
        data["images"] = usable
        data["warnings"] = list(dict.fromkeys(data.get("warnings", []) + image_issues))
        attempt = job["attempt"]
        data.update(rewrite_count=attempt, stage="review_required")
        if job["phase"] == "write":
            data["verification"] = [source_verification(data)] + [{"name": name, "state": "pending", "detail": "等待本轮写作及独立审核。"} for name in CHECKS]
            raw = llm.generate(data["task"], data, previous_draft=job["previous_draft"], feedback=job["feedback"])
            try:
                draft = validate_draft(raw, data)
            except ValueError as error:
                detail = str(error)
                data["verification"] = [{"name": "输出结构与证据引用", "state": "fail", "detail": detail}]
                data["review_issues"] = [detail]
                data["review_history"].append({"rewrite_count": attempt, "decision": "revise", "checks": deepcopy(data["verification"]), "issues": [detail]})
                job.update(attempt=attempt + 1, previous_draft=raw if isinstance(raw, dict) else None, feedback=[detail], phase="write")
                data["stage"] = "rewriting"
                return {**result, "status": "ok", "message": f"第{attempt + 1}轮输出校验未通过，正带着具体问题自动重写。"}
            job.update(candidate=draft, phase="review")
        draft = job["candidate"]
        local_issues = draft_issues(draft, data)
        layout = platform_format_issues(draft, data)
        if layout:
            local_issues["平台格式"] = layout
        if not minimum <= len(draft["image_plan"]) <= maximum:
            local_issues["平台图数"] = [f"请从现有候选图选择{minimum}–{maximum}张并逐一标注插图位置。"]
        data["verification"] = [source_verification(data)] + [
            {"name": name, "state": "fail" if name in local_issues else "pending", "detail": "；".join(local_issues[name]) if name in local_issues else "本地检查未发现问题，等待本轮独立审核。"} for name in CHECKS
        ]
        data["review_issues"] = [name + "：" + "；".join(issues) for name, issues in local_issues.items()]
        try:
            review = llm.review(data, draft)
            checks = validate_review(review, data, draft)
        except ValueError as error:
            data.update(stage="review_retry", review_issues=[str(error)])
            return {**result, "status": "ok", "message": "审核返回不完整，正在重新审核本稿；不会跳过检查交付。"}
        data["verification"] = [source_verification(data)] + checks
        data["review_history"].append({"rewrite_count": attempt, "decision": review["decision"], "checks": checks, "issues": review["issues"]})
        data["review_suggestions"] = review.get("suggestions", [])
        pending = list(review["pending_items"])
        feedback = list(review["revision_requests"])
        for check in checks:
            if check["state"] == "fail":
                feedback.append(check["name"] + "：" + check["detail"])
            elif check["state"] == "pending":
                pending.append(check["name"] + "：" + check["detail"])
        if not minimum <= len(draft["image_plan"]) <= maximum:
            feedback.append(f"请从现有候选图中选择{minimum}–{maximum}张，逐一添加正文插图标记和配图方案。")
        feedback = list(dict.fromkeys(feedback))
        data["review_issues"] = list(dict.fromkeys(review["issues"] + feedback + pending))
        if review["decision"] == "blocked" and (not feedback or any(check["name"] == "产品事实" and check["state"] != "pass" for check in checks)):
            data.update(stage="blocked")
            data.pop("_rewrite", None)
            return {**result, "status": "error", "message": blocked_message(data)}
        if feedback:
            job.update(attempt=attempt + 1, previous_draft=draft, feedback=feedback, candidate=None, phase="write")
            data["stage"] = "rewriting"
            return {**result, "status": "ok", "message": f"第{attempt + 1}轮审核发现可修改问题，正在自动重写并复核。"}
        image_issues = delivery_issues(data, draft)
        if image_issues:
            return _await_images(result, image_issues)
        data.update(draft=draft, stage="pending_verification" if pending else "completed")
        data.pop("_rewrite", None)
        return {**result, "status": "ok", "message": f"已完成{attempt}次退回修改，本轮审核没有未通过项。" + ("图片等外部事项仍待核验，当前交付为待核验草稿。" if pending else "")}
    except (llm.ModelError, ValueError) as error:
        data["draft"] = None
        if data.get("_rewrite"):
            data["stage"] = "generation_paused"
        return {**result, "status": "error", "message": redact(str(error)) + " 本次进度已保留，可点击继续写作与审核。"}


def chat(user_text: str, history: list, *, state: dict | None = None) -> dict:
    if not isinstance(user_text, str) or not user_text.strip():
        return {"status": "error", "message": "请输入需求。"}
    if not isinstance(history, list) or any(not isinstance(item, dict) or item.get("role") not in ("user", "assistant") or not isinstance(item.get("content"), str) for item in history):
        return {"status": "error", "message": "聊天记录格式不符合共同约定。"}
    if len(user_text) > 6000 or any(len(item["content"]) > 12000 for item in history):
        return {"status": "error", "message": "这次对话过长，请精简输入或清空后重新开始。"}
    if state is not None and not isinstance(state, dict):
        return {"status": "error", "message": "本次需求记录异常，请清空后重试。"}
    try:
        analysis = llm.understand(user_text.strip(), deepcopy(history[-60:]), deepcopy(state))
        if re.search(r"matebookgt4(?!\d)", normalized(user_text)) and "matebookgt14" not in normalized(user_text):
            analysis = deepcopy(analysis)
            if not isinstance(analysis.get("requirements"), dict):
                raise ValueError("模型没有返回完整需求清单，请重试。")
            analysis["requirements"]["product"] = {"state": "ambiguous", "value": "MateBook GT 4"}
            analysis.setdefault("questions", {})["product"] = "你写的是 MateBook GT 4，是否指此前已查到资料的 MateBook GT 14？"
        data = next_state(analysis, state, PLATFORMS)
        message = confirmation_text(data["task"]) if data["stage"] == "ready_for_confirmation" else data["question"]
        return {"status": "ok", "message": message, "data": data}
    except (llm.ModelError, ValueError) as error:
        return {"status": "error", "message": redact(str(error))}


def body_sections(data: dict) -> list[dict]:
    draft = data["draft"]
    pictures = {item["source_id"]: item for item in data["images"]}
    plans = {item["source_id"]: item for item in draft["image_plan"]}
    sections, start = [], 0
    for marker in re.finditer(r"【配图\s*(I\d+)\s*】", draft["body"]):
        identifier = marker[1]
        if identifier not in plans or identifier not in pictures:
            continue
        sections.append({"kind": "text", "text": draft["body"][start:marker.start()]})
        item, picture = plans[identifier], pictures[identifier]
        caption = f"配图 {identifier}：{picture['title']}｜来源：{picture['knowledge_base']}｜{source_location(picture)}｜说明：{item['description']}｜位置：{item['position']}｜型号待核验"
        sections.append({"kind": "image", "picture": picture, "caption": caption})
        start = marker.end()
    sections.append({"kind": "text", "text": draft["body"][start:]})
    return sections


def body_with_images(data: dict, *, image_links: dict | None = None) -> str:
    from image_assets import markdown_image

    if delivery_issues(data):
        return ""
    parts = []
    for section in body_sections(data):
        if section["kind"] == "text":
            parts.append(section["text"])
        else:
            picture = section["picture"]
            href = image_links.get(picture["source_id"]) if image_links is not None else None
            parts.append(markdown_image(picture, href) + "\n\n" + "【" + section["caption"] + "】")
    return "".join(parts)


def source_location(item: dict) -> str:
    if item.get("local_path"):
        return "本地测试文件：" + item["local_path"] + "（本次未查询 ima）"
    path = item.get("knowledge_path", "")
    return ("已取得目录层级：" + path + "；" + item.get("path_note", "未确认是否为完整路径")) if path else "未取得库内目录路径，请按资料标题在 ima 中定位"


def draft_markdown(data: dict, *, image_links: dict | None = None) -> str:
    draft = data.get("draft")
    if not draft or delivery_issues(data):
        return ""
    tags = " ".join("#" + tag.strip().lstrip("#") for tag in draft["tags"] if tag.strip().lstrip("#"))
    notice = "> 当前为本地资料测试，未调用 ima 知识库。" if data.get("material_mode") == "local" else ""
    return "\n\n".join(part for part in (notice, "# " + draft["title"], draft["intro"], body_with_images(data, image_links=image_links), draft["cta"], tags) if part)


def export_markdown(result: dict, *, image_links: dict | None = None) -> str:
    data = result["data"]
    task, strategy = data["task"], data["strategy"]
    notice = "当前为联调模拟结果，不是真实知识库运行记录。" if data.get("stage") == "mock" else "当前记录包含待核验内容，不能视为可发布成稿。"
    if data.get("material_mode") == "local":
        notice = "当前为本地资料测试，未调用 ima 知识库。" + notice
    lines = ["# 华为 PC 营销任务记录", "", "> " + notice, "", "## 一、场景判断", ""]
    for label, key in (("产品", "product"), ("目标人群", "audience"), ("场景", "scenario"), ("平台", "platform"), ("大致字数", "word_count"), ("配图数量", "image_count"), ("价格区间", "price_range"), ("内容目标", "goal"), ("补充要求", "extra")):
        lines.append(f"- {label}：{task.get(key) or '未补充'}")
    source_names = "、".join(dict.fromkeys(source["knowledge_base"] for source in data["sources"]))
    lines += ["", strategy["focus"], "", "内容结构：" + strategy["structure"], "", "## 二、知识库依据", "", "来源：" + source_names, ""]
    for source in data["sources"]:
        lines += [f"### {source['source_id']} · {source['title']}", "", source_location(source), "", "原文节选（仅供核对，非成稿）：", ""]
        lines.extend("> " + line for line in source["text"][:500].splitlines())
        lines += [""]
    image_issues = delivery_issues(data)
    draft = data.get("draft") if not image_issues else None
    if draft:
        lines += ["### 成稿事实与对应原文", ""]
        for claim in draft["claims"]:
            lines += [f"- {claim['text']}（{claim['source_id']}）", "> " + claim["quote"], ""]
    lines += ["### 候选图片", ""]
    for picture in data["images"]:
        lines += [f"- {picture['source_id']} · {picture['title']}；来源：{picture['knowledge_base']}；{source_location(picture)}；说明：{picture['description']}；型号待核验。"]
    lines += ["## 三、图文软文", ""]
    if draft:
        lines += ["> 待核验草稿；审核意见与未完成事项见第四部分。", "", "场景切入点：" + draft["angle"], "", draft_markdown(data, image_links=image_links), ""]
    elif image_issues:
        lines += ["配图检查未通过，请重新读取或补充真实图片；本次记录不包含正文。", ""]
    elif data.get("stage") in ("blocked", "format_blocked"):
        lines += ["本次审核或格式检查未通过，不展示文案；请先解决第四部分的问题。", ""]
    elif data.get("stage") in ("rewriting", "review_retry"):
        lines += ["正在根据问题清单自动修改与复核，通过后展示正文。", ""]
    elif data.get("stage") == "generation_paused":
        lines += ["自动修改与审核已暂停，进度保留；可回到页面继续。", ""]
    elif data.get("stage") == "awaiting_materials":
        lines += ["等待补充对应型号资料或图片，补齐后可继续生成。", ""]
    else:
        lines += ["当前尚未生成文案。", ""]
    lines += ["", "## 四、核验结果", ""]
    for check in data["verification"]:
        label = {"pass": "✅ 已检查", "pending": "待核验", "fail": "❌ 未通过"}[check["state"]]
        lines.append(f"- {check['name']}（{label}）：{check['detail']}")
    lines.extend("- 真实配图（❌ 未通过）：" + detail for detail in image_issues)
    lines.extend("- 待确认：" + warning for warning in data.get("warnings", []) + data.get("review_issues", []))
    lines.extend("- 可选建议：" + suggestion for suggestion in data.get("review_suggestions", []))
    return "\n".join(lines) + "\n"
