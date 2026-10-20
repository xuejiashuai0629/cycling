"""Allowlisted diagnostics: no raw responses, credentials or rejected copy."""

from datetime import datetime, timezone
import json

from config import KNOWLEDGE_BASE, ROOT, redact, settings


def ima_configured() -> bool:
    values = settings()
    return all(values.get(field, "").strip() for field in ("IMA_OPENAPI_CLIENTID", "IMA_OPENAPI_APIKEY"))


def check_ima_connection() -> dict:
    from tools import ImaClient, KnowledgeError, items
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "knowledge_base": KNOWLEDGE_BASE}
    try:
        client = ImaClient(total_timeout=45)
        kb_id = client.knowledge_base()
        client.call("get_knowledge_base", {"ids": [kb_id]})
        page = client.call("get_knowledge_list", {"knowledge_base_id": kb_id, "cursor": "", "limit": 5})
        summary.update(status="ok", message="本次实时验证成功", response_summary={"业务码": 0, "本页条目数": len(items(page))}, scope="已验证指定库搜索、详情和目录读取；未逐份核对原文或图片。")
    except (KnowledgeError, ValueError, OSError) as error:
        summary.update(status="error", message=redact(str(error)) if not isinstance(error, OSError) else "连接检查未完成，请检查网络后重试。")
    return summary


def save_materials_summary(result: dict, product: str, *, mode: str = "ima") -> None:
    """Keep the actual retrieval failure without saving raw API responses or IDs."""
    data = result.get("data", {})
    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_base": KNOWLEDGE_BASE if mode == "ima" else "本地测试资料", "product": redact(product), "material_mode": mode,
        "status": result["status"], "message": redact(result["message"]),
        "source_count": len(data.get("sources", [])), "image_count": len(data.get("images", [])),
    }
    try:
        directory = ROOT / ".ima-state"
        directory.mkdir(exist_ok=True)
        (directory / "last_materials.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Diagnostics must never change whether the actual read succeeded.
        pass


def save_review_summary(result: dict) -> None:
    data = result["data"]
    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": result["status"], "message": redact(result["message"]),
        "task": {field: redact(data["task"][field]) for field in ("product", "platform", "scenario", "audience")},
        "stage": data.get("stage", ""), "rewrite_count": data.get("rewrite_count", 0),
        "material_mode": data.get("material_mode", "ima"),
        "checks": [{"name": item["name"], "state": item["state"], "detail": redact(item["detail"])} for item in data["verification"]],
        "issues": [redact(item) for item in data.get("review_issues", [])],
        "source_count": len(data.get("sources", [])), "image_count": len(data.get("images", [])),
    }
    try:
        directory = ROOT / ".ima-state"
        directory.mkdir(exist_ok=True)
        (directory / "last_review.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        data.setdefault("warnings", []).append("本次核验记录未能写入本地；请保留页面核验结果。")
