"""Shared DeepSeek JSON adapter for understanding, writing and review."""

import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from config import ROOT, settings

ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-flash"
REQUEST_TIMEOUT = 60


class ModelError(RuntimeError):
    pass


class ModelNotConfigured(ModelError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def is_ready() -> bool:
    return bool(settings().get("DEEPSEEK_API_KEY", "").strip())


def complete_json(system: str, payload: dict, *, max_tokens: int = 2500) -> dict:
    values = settings()
    key = values.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ModelNotConfigured("尚未配置 DeepSeek。请在项目 .env 的 DEEPSEEK_API_KEY= 后填写密钥并保存，不要把密钥发到聊天里。")
    body = {
        "model": values.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL,
        "messages": [{"role": "system", "content": system + "\n仅返回有效 JSON 对象。"}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        "response_format": {"type": "json_object"}, "stream": False,
        "thinking": {"type": "disabled"}, "max_tokens": max_tokens,
    }
    request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ModelError("DeepSeek 返回内容过大，请缩小任务后重试。")
        envelope = json.loads(raw)
        choice = envelope["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ModelError("DeepSeek 输出未完整结束，本次不使用不完整结果，请重试。")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ModelError("DeepSeek 返回了空内容，请重试。")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ModelError("DeepSeek 没有返回 JSON 对象，请重试。")
        return result
    except HTTPError as error:
        reasons = {400: "请求格式或模型参数不受支持", 401: "密钥无效", 402: "账号余额或额度不足", 403: "账号无权访问", 404: "模型不存在或不可用", 429: "请求过于频繁"}
        error.close()
        raise ModelError(f"DeepSeek 请求失败（{error.code}）：{reasons.get(error.code, '服务暂时不可用')}。请检查配置后重试。") from None
    except (TimeoutError, socket.timeout):
        raise ModelError("DeepSeek 请求等待超时，请稍后重试。") from None
    except URLError:
        raise ModelError("无法连接 DeepSeek，请检查网络后重试。") from None
    except OSError:
        raise ModelError("DeepSeek 连接中断，请稍后重试。") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("DeepSeek 返回格式不完整，本次不使用该结果，请重试。") from None


def prompt(name: str) -> str:
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def system_prompt() -> str:
    return prompt("system")


def evidence_pool(text: str, *, max_items: int = 80) -> list[str]:
    """Give the writer exact, short spans it can copy without paraphrasing."""
    pool = []
    for raw in text.splitlines():
        value = raw.strip()
        if len(value) < 8 or len(value) > 400:
            continue
        if re.search(r"文档用途|使用对象|内容性质|事实边界|交付格式|正文结构|输出 JSON|合规与校验|不得|不要|禁止|应当|必须", value):
            continue
        pool.append(value)
    return pool[:max_items]


def evidence_for_model(data: dict) -> dict:
    from content_review import writing_contract
    pool = []
    for source in data["sources"]:
        for quote in evidence_pool(source["text"]):
            pool.append({"source_id": source["source_id"], "quote": quote})
    return {
        "task": data["task"], "strategy": data["strategy"],
        "writing_contract": writing_contract(data),
        "sources": [{"source_id": item["source_id"], "title": item["title"], "text": item["text"][:16000]} for item in data["sources"]],
        "evidence_pool": pool,
        "images": [{"source_id": item["source_id"], "title": item["title"]} for item in data["images"]],
        "limitations": "每份文字最多提供前16000字符；图片只提供标题，尚未检查图像内容。",
    }


def understand(user_text: str, history: list, previous: dict | None) -> dict:
    return complete_json(prompt("understanding") + "\n" + prompt("scenario_reference"), {"history": history, "user_text": user_text, "previous": previous})


def select_product(task: dict, candidates: list[dict]) -> str:
    result = complete_json(prompt("product_selection"), {"task": task, "candidates": candidates}, max_tokens=200)
    product = result.get("product", "")
    if not isinstance(product, str) or product.strip() not in {item["product"] for item in candidates}:
        raise ModelError("模型没有从候选型号中选择，请重试。")
    return product.strip()


def generate(task: dict, evidence: dict, *, previous_draft: dict | None = None, feedback: list[str] | None = None) -> dict:
    payload = evidence_for_model(evidence)
    if previous_draft is not None:
        payload.update(previous_draft=previous_draft, revision_requests=feedback or [])
    return complete_json(system_prompt() + "\n" + prompt("scenario_reference") + "\n" + prompt("writing"), payload, max_tokens=6000)


def review(data: dict, draft: dict) -> dict:
    return complete_json(system_prompt() + "\n" + prompt("review"), {**evidence_for_model(data), "draft": draft}, max_tokens=4000)
