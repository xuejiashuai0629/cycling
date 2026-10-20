"""Conservative local red-line checks; semantic and visual review remain necessary."""

from decimal import Decimal
import re
import unicodedata

from product_identity import different_model

QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(?:kg|gb|tb|wh|mah|hz|nits?|g|w|k|英寸|小时|分钟|毫米|毫秒|元|克|瓦|%)", re.I)
PROMOTION = re.compile(r"(?:直降|立减|满减|补贴|优惠价|折后价|到手价|券后价|售价|价格为|低至|仅售|现价|现货充足|库存充足|保证有货|随时有货|现货|有货)")
ABSOLUTE = re.compile(r"(?:最好(?:的)?(?:轻薄本|电脑|笔记本|屏幕|性能|选择)|(?:市面上|行业|全网|全球|世界|同类|国产|销量|性能).{0,6}(?:最好|最强|第一)|无敌|遥遥领先|天花板|[第全]一(?:品牌|轻薄本)|100%保证|百分之百保证)")
ATTACK = re.compile(r"(?:吊打|碾压|秒杀|完爆|垃圾|智商税)")
EXPERIENCE = re.compile(r"(?:我|本人|亲身|亲测|实测|亲自).{0,20}(?:用了|使用了|用过|体验了|测了|测过|测试了|入手|上手|两周|半个月)|(?:亲测|实测|亲身体验|真实体验|真实感受)")


def norm(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).lower()


def quantities(value: str) -> set[str]:
    return {norm(match.group()) for match in QUANTITY.finditer(value)}


def visible_text(draft: dict) -> str:
    parts = [draft[field] for field in ("title", "intro", "body", "cta")]
    parts += draft.get("tags", [])
    parts += [item["description"] for item in draft.get("image_plan", [])]
    return "\n".join(parts)


def evidence_issues(data: dict) -> list[str]:
    issues = []
    for source in data["sources"]:
        if different_model(data["task"]["product"], source["title"]):
            issues.append(f"资料型号与任务不同：{source['source_id']} · {source['title']}；需重新检索。")
    # Only simple, explicitly labeled scalar fields are parsed here. Complex
    # tables, optional configurations and semantic contradictions need A3.
    fields = {
        "重量": r"(?:整机|机身|产品)?重量",
        "屏幕尺寸": r"屏幕尺寸",
        "刷新率": r"(?:屏幕)?刷新率",
        "电池容量": r"电池容量",
        "内存": r"内存(?:容量)?",
        "存储容量": r"(?:硬盘|存储)容量",
    }
    facts = {}
    for source in data["sources"]:
        for field, label in fields.items():
            pattern = rf"^\s*[-*|\s]*(?:{label})\s*[:：]\s*(\d+(?:\.\d+)?)\s*(kg|g|克|英寸|hz|wh|mah|gb|tb)\s*[。;；|\s]*$"
            for match in re.finditer(pattern, source["text"], re.I | re.M):
                value, unit = Decimal(match[1]), match[2].lower()
                if unit == "kg":
                    value, unit = value * 1000, "g"
                if unit == "克":
                    unit = "g"
                facts.setdefault(field, []).append((value, unit, source, match.group().strip()))
    for field, entries in facts.items():
        if len({item[2]["source_id"] for item in entries}) > 1 and len({(item[0], item[1]) for item in entries}) > 1:
            details = "；".join(f"{item[2]['source_id']} · {item[2]['title']}：{item[3]}" for item in entries)
            issues.append(f"资料冲突（{field}）：{details}。请人工确认配置与版本，停止成稿。")
    return issues


def draft_issues(draft: dict, data: dict) -> dict[str, list[str]]:
    issues = {"产品事实": [], "图片型号": [], "营销合规": []}
    text = visible_text(draft)
    sources = {source["source_id"]: source for source in data["sources"]}
    claims = draft["claims"]
    if different_model(data["task"]["product"], text):
        issues["产品事实"].append("文案包含与本次任务不同的产品型号；请只使用当前型号。")
    # A model cannot bypass the evidence gate by leaving claims empty or citing
    # a valid but unrelated sentence containing a different numeric value.
    for point in draft["selling_points"]:
        if not any(point in claim["text"] for claim in claims):
            issues["产品事实"].append("正文卖点缺少对应的事实点与原文摘录：" + point + "。selling_points只列正文中的产品事实原句，必须逐字包含在某条claims.text内；场景建议另写，不混入卖点清单。quote须保留原文限定条件。")
    for claim in claims:
        if quantities(claim["text"]) - quantities(claim["quote"]):
            issues["产品事实"].append("事实点中的数值/单位与其原文摘录不一致：" + claim["text"])
    for sentence in re.split(r"[\n。！？!?]+", re.sub(r"【配图\s*I\d+\s*】", "", text)):
        sentence = sentence.strip()
        if not sentence:
            continue
        for quantity in quantities(sentence):
            if not any(quantity in quantities(claim["text"]) and quantity in quantities(claim["quote"]) and norm(claim["text"]).rstrip("。.!！?？") in norm(sentence) for claim in claims):
                issues["产品事实"].append("文案数值未逐句列入有效证据清单：" + quantity)
        if PROMOTION.search(sentence):
            supported = any(norm(sentence) in norm(claim["text"]) and PROMOTION.search(claim["quote"]) and quantities(sentence) <= quantities(claim["quote"]) and claim["source_id"] in sources for claim in claims)
            if not supported:
                issues["营销合规"].append("价格、优惠、补贴或库存承诺缺少对应正式资料；请删除，改为到店咨询，或先补充正式文件。")
        if ABSOLUTE.search(sentence):
            issues["营销合规"].append("包含绝对化宣传或无条件保证；请改为有依据的具体表述。")
        if ATTACK.search(sentence):
            issues["营销合规"].append("包含攻击性比较或贬低用语；请只陈列本方有依据的卖点。")
        if EXPERIENCE.search(sentence):
            issues["营销合规"].append("包含亲测、实测或个人使用经历；本工具不能替用户编造体验，请改为场景化建议。")
    images = {item["source_id"]: item for item in data["images"]}
    for plan in draft["image_plan"]:
        picture = images[plan["source_id"]]
        if different_model(data["task"]["product"], picture["title"]):
            issues["图片型号"].append("图片标题明确指向其他型号：" + picture["title"])
    return {name: list(dict.fromkeys(items)) for name, items in issues.items() if items}
