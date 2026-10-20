"""Project-local configuration; never print credential values."""

from pathlib import Path
import os
import shutil
import sys
import re

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
KNOWLEDGE_BASE = "任德耀新建华为PC知识库"
PYTHON_VERSION = (3, 15, 0)
STREAMLIT_VERSION = "1.66.0"
API_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 15
TOTAL_TIMEOUT = 90
MAX_API_CALLS = 22

PLATFORMS = {
    "公众号": {"images": (2, 4), "structure": "标题、导语、分段正文、结尾行动引导", "tone": "清晰、具体，解释使用场景", "cta": "邀请了解产品或到店体验"},
    "小红书": {"images": (3, 6), "structure": "口语化标题、短段落、场景说明、话题标签", "tone": "自然口语，不伪造亲身使用体验", "cta": "邀请交流需求或了解具体配置"},
    "门店朋友圈": {"images": (1, 3), "structure": "短文案、一个核心卖点、咨询或到店引导", "tone": "简短、亲切、突出一个需求", "cta": "咨询或到店体验，不虚构库存与优惠"},
    "门店海报": {"images": (1, 1), "structure": "主标题、副标题、三个以内卖点、活动说明", "tone": "醒目、简洁，信息层次清楚", "cta": "邀请到店；活动信息缺失时不编造"},
}


def settings() -> dict[str, str]:
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    values.update(os.environ)
    return values


def redact(message: str) -> str:
    for key, value in settings().items():
        if value and ("KEY" in key or "TOKEN" in key or "CLIENTID" in key):
            message = message.replace(value, "[已隐藏]")
    message = re.sub(r"https?://[^\s\"'<>]+", "[链接已隐藏]", message, flags=re.I)
    message = re.sub(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[^\s,\"'}]+", r"\1[已隐藏]", message)
    message = re.sub(r"(?i)((?:media_id|folder_id|kb_id|knowledge_base_id)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}]+", r"\1[已隐藏]", message)
    return message[:500]


def ima_environment() -> dict[str, str]:
    values = settings()
    required = ("IMA_OPENAPI_CLIENTID", "IMA_OPENAPI_APIKEY")
    if any(not values.get(key) for key in required):
        raise ValueError("请先在项目的 .env 中填写 ima 的两项访问凭证。")
    environment = os.environ.copy()
    for key in ("IMA_CLIENT_ID", "IMA_API_KEY", "IMA_BASE_URL", "IMA_FORCE_UPDATE_CHECK", "IMA_SKILL_VERSION"):
        environment.pop(key, None)
    environment.update({key: values[key] for key in required})
    environment["IMA_LAST_CHECK_FILE"] = str(ROOT / ".ima-state" / "last_update_check")
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def runtime_issues() -> list[str]:
    issues = []
    if sys.version_info[:3] != PYTHON_VERSION:
        issues.append("本项目约定 Python 3.15.0，当前为 " + ".".join(map(str, sys.version_info[:3])) + "。请切换解释器，勿自动改版本。")
    if not shutil.which("node"):
        issues.append("找不到 Node.js；它负责运行官方 ima 读取脚本。")
    if not (ROOT / "ima-skill" / "ima_api.cjs").is_file():
        issues.append("缺少完整官方 ima-skill 目录，请保留完整解压内容。")
    return issues
