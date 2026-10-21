"""Structural and evidence gates supplement, not replace, model review."""

from copy import deepcopy
import re

from safety_review import draft_issues

CHECKS = ("产品事实", "图片型号", "平台图数", "平台格式", "场景匹配", "营销合规")


def word_count_limits(value: str | None) -> dict:
    """Turn a request like “500–800字” into a hard delivery window."""
    numbers = [int(item) for item in re.findall(r"\d+", value or "")]
    if not numbers:
        return {"minimum": None, "maximum": None, "detail": value or "未指定"}
    minimum = numbers[0]
    maximum = numbers[1] if len(numbers) > 1 else round(minimum * 1.25)
    return {"minimum": minimum, "maximum": maximum, "detail": value or ""}


def writing_contract(data: dict) -> dict:
    """Give the writer the selected platform's concrete requirements up front."""
    platform = data["task"]["platform"]
    rules = {
        "公众号": {"outline": ["标题", "导语", "场景问题段", "有据解决方式段", "结尾行动引导"], "minimum_paragraphs": 2, "selling_points": "每条都是正文里的事实原句，逐条有证据", "tags": "空数组"},
        "小红书": {"outline": ["口语化标题", "短导语", "具体场景困扰", "有据特征及自然选择建议", "交流引导", "话题标签"], "minimum_paragraphs": 2, "selling_points": "每条都是正文里的事实原句，不伪造体验", "tags": "至少一个话题，不含#"},
        "门店朋友圈": {"outline": ["简短标题", "短导语", "本次场景的一件事＋一项有据特征", "一次咨询或到店引导"], "minimum_paragraphs": 1, "selling_points": "恰好一项事实特征，不把多项功能拼成一条", "tags": "空数组"},
        "门店海报": {"outline": ["主标题放title", "副标题放intro", "1–3行- 卖点放body", "一句行动引导放cta"], "minimum_paragraphs": 1, "selling_points": "1–3项，逐字对应body中的项目符号", "tags": "空数组"},
    }
    minimum, maximum = data["strategy"]["images"]
    words = word_count_limits(data["task"].get("word_count"))
    return {
        "platform": platform, **rules[platform],
        "tone": data["task"].get("tone") or "专业导购",
        "image_count": {"minimum": minimum, "maximum": maximum},
        "word_count": words,
        "allowed_image_ids": [item["source_id"] for item in data["images"]],
        "image_positions": "image_plan.position使用第1段后、第2段后等明确位置；只数正文文字段落，图片标记不算段落。程序按此插入图片，不得使用待定位置。也可在body直接写对应的【配图 I编号】。",
        "fact_mapping": "selling_points只列正文中的产品事实原句，不混入建议；每项逐字包含在某条claims.text内，quote逐字摘自对应原文。所有数值和限定条件都必须有据。",
        "reader_voice": "title/intro/body/cta面向读者；不写内部审核过程，不以免责声明代替内容，不编造价格、库存、适配或实测。",
    }


def insert_planned_images(draft: dict) -> dict:
    """Render explicit paragraph positions without changing prose or sources."""
    clean = deepcopy(draft)
    body = clean["body"]
    # Existing/partial markers are checked as written. Never guess around a
    # malformed marker or duplicate an image that the writer already inserted.
    if "配图" in body or "![" in body or re.search(r"<img\b", body, re.I):
        return clean
    paragraphs = [part for part in re.split(r"\n\s*\n", body) if part.strip()]
    if not paragraphs:
        return clean
    insertions = {}
    numbers = {char: index for index, char in enumerate("一二三四五六七八九十", 1)}
    for image in clean["image_plan"]:
        position = image["position"].strip()
        match = re.fullmatch(r"第\s*([1-9]\d*|[一二三四五六七八九十])\s*段(?:落)?后", position)
        if match:
            token = match[1]
            index = int(token) if token.isdecimal() else numbers[token]
        elif position in ("正文后", "正文末尾", "正文结尾", "文末"):
            index = len(paragraphs)
        else:
            return clean
        if not 1 <= index <= len(paragraphs):
            return clean
        insertions.setdefault(index, []).append(f"【配图 {image['source_id']}】")
    for index, markers in insertions.items():
        paragraphs[index - 1] += "\n\n" + "\n\n".join(markers)
    clean["body"] = "\n\n".join(paragraphs)
    return clean


def validate_draft(draft: dict, data: dict) -> dict:
    if not isinstance(draft, dict):
        raise ValueError("写作模型返回格式异常，请重试。")
    for field in ("title", "intro", "body", "cta"):
        if not isinstance(draft.get(field), str) or (field in ("title", "body") and not draft[field].strip()):
            raise ValueError("写作模型没有返回完整标题与正文，请重试。")
    if not isinstance(draft.get("tags"), list) or any(not isinstance(tag, str) for tag in draft["tags"]):
        raise ValueError("写作模型返回的标签格式异常。")
    if not isinstance(draft.get("angle"), str) or not draft["angle"].strip():
        raise ValueError("文案缺少针对本次场景的内容切入点。")
    if not isinstance(draft.get("selling_points"), list) or any(not isinstance(point, str) or not point.strip() for point in draft["selling_points"]):
        raise ValueError("文案卖点清单格式异常。")
    text = "\n".join(draft[field] for field in ("title", "intro", "body", "cta"))
    if len(text) > 30000:
        raise ValueError("文案过长，请缩小任务后重试。")
    sources = {item["source_id"]: item for item in data["sources"]}
    if not isinstance(draft.get("claims"), list):
        raise ValueError("文案缺少事实证据清单。")
    for claim in draft["claims"]:
        if not isinstance(claim, dict) or not isinstance(claim.get("source_id"), str) or claim["source_id"] not in sources or not isinstance(claim.get("quote"), str) or not claim["quote"].strip() or claim["quote"] not in sources[claim["source_id"]]["text"] or not isinstance(claim.get("text"), str) or not claim["text"].strip() or claim["text"] not in text:
            raise ValueError("文案含有无法对应原文的事实引用，本次不展示该成稿。")
    images = {item["source_id"] for item in data["images"]}
    if not isinstance(draft.get("image_plan"), list):
        raise ValueError("文案缺少配图方案。")
    seen = set()
    for image in draft["image_plan"]:
        if not isinstance(image, dict) or not isinstance(image.get("source_id"), str) or image["source_id"] not in images or image["source_id"] in seen or any(not isinstance(image.get(key), str) or not image[key].strip() for key in ("position", "description")):
            raise ValueError("文案配图不在本次素材中、存在重复或缺少位置说明。")
        seen.add(image["source_id"])
    return insert_planned_images(draft)


def platform_format_issues(draft: dict, data: dict) -> list[str]:
    """Check visible structure locally; tone and scene semantics need A3."""
    platform = data["task"]["platform"]
    issues = []
    if not draft["intro"].strip():
        issues.append("缺少导语；海报需将副标题放在 intro。")
    if not draft["cta"].strip():
        issues.append("缺少适合本平台的行动引导。")
    body = re.sub(r"【配图\s*I\d+\s*】", "", draft["body"]).strip()
    words = word_count_limits(data["task"].get("word_count"))
    length = len(re.sub(r"\s", "", body))
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    if platform in ("公众号", "小红书") and len(paragraphs) < 2:
        issues.append("本平台正文需分段，至少包含两个非空段落。")
    if platform == "小红书" and not any(tag.strip().lstrip("#").strip() for tag in draft["tags"]):
        issues.append("小红书缺少话题标签。")
    if platform == "门店朋友圈" and len(draft["selling_points"]) != 1:
        issues.append("门店朋友圈必须只围绕一个核心卖点展开。")
    if platform == "门店海报":
        bullets = [line[2:].strip() for line in body.splitlines() if line.startswith("- ")]
        if not 1 <= len(draft["selling_points"]) <= 3 or bullets != draft["selling_points"] or any(line.strip() and not line.startswith("- ") for line in body.splitlines()):
            issues.append("海报正文必须为1–3条卖点，每条使用“- ”单列，并与卖点清单一致；不可套用文章正文。")
    for point in draft["selling_points"]:
        if point not in draft["body"]:
            issues.append("卖点清单必须与实际正文中的卖点一致。")
            break
    if words["minimum"] and length < words["minimum"]:
        issues.append(f"正文约{length}字，低于本次要求的不少于{words['minimum']}字。")
    if words["maximum"] and length > round(words["maximum"] * 1.2):
        issues.append(f"正文约{length}字，明显超过本次要求的{words['maximum']}字左右。")
    markers = re.findall(r"【配图\s*(I\d+)\s*】", draft["body"])
    planned = [picture["source_id"] for picture in draft["image_plan"]]
    if sorted(markers) != sorted(planned):
        issues.append(f"正文插图标记与配图方案不对应：实际{markers}，应有{planned}。请在实际插图位置逐一标注【配图 I编号】，或将position写成有效的第1段后/第2段后，不遗漏或重复。")
    return issues


def validate_review(review: dict, data: dict, draft: dict) -> list[dict]:
    if not isinstance(review, dict) or review.get("decision") not in ("pass", "revise", "blocked") or not isinstance(review.get("checks"), list) or not isinstance(review.get("issues"), list) or any(not isinstance(item, str) for item in review["issues"]):
        raise ValueError("审核模型返回格式异常，成稿保持待核验。")
    for field in ("revision_requests", "pending_items"):
        if not isinstance(review.get(field), list) or any(not isinstance(item, str) or not item.strip() for item in review[field]):
            raise ValueError("审核模型未区分可改写问题和外部待确认事项。")
    if not isinstance(review.get("suggestions", []), list) or any(not isinstance(item, str) or not item.strip() for item in review.get("suggestions", [])):
        raise ValueError("审核模型的可选建议格式异常。")
    if review["decision"] == "revise" and not review["revision_requests"] and not review["pending_items"]:
        raise ValueError("审核要求修改但没有给出修改清单或待确认事项，需重新审核。")
    checks = {}
    for item in review["checks"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or item["name"] not in CHECKS or item["name"] in checks or item.get("state") not in ("pass", "fail", "pending") or not isinstance(item.get("detail"), str):
            raise ValueError("审核模型检查项目不完整或重复。")
        checks[item["name"]] = deepcopy(item)
    if set(checks) != set(CHECKS):
        raise ValueError("审核模型没有检查全部六个项目。")
    if checks["图片型号"]["state"] != "fail":
        checks["图片型号"].update(state="pending", detail="本轮只向模型提供图片标题，尚未核对图像中的具体型号和版本。")
    for name, issues in draft_issues(draft, data).items():
        checks[name].update(state="fail", detail="；".join(issues))
    minimum, maximum = data["strategy"]["images"]
    count = len(draft["image_plan"])
    checks["平台图数"].update(state="pass" if minimum <= count <= maximum else "fail", detail=f"配图方案共 {count} 张；本平台要求 {minimum}–{maximum} 张。")
    format_issues = platform_format_issues(draft, data)
    if format_issues:
        checks["平台格式"].update(state="fail", detail="；".join(format_issues))
    return [checks[name] for name in CHECKS]
