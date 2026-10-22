"""Lightweight OCR evidence for marketing images."""

from typing import Any

from image_assets import _read_image

_engine = None


def _ocr_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def analyze_picture(picture: dict) -> dict:
    """Read visible text in an image; this is OCR evidence, not full vision."""
    try:
        path, _ = _read_image(picture)
        result, _ = _ocr_engine()(str(path))
        lines = []
        for item in result or []:
            if len(item) >= 2 and isinstance(item[1], str) and item[1].strip():
                lines.append(item[1].strip())
        text = "\n".join(lines)[:2000]
        return {"status": "ok" if text else "no_text", "ocr_text": text, "lines": lines[:20]}
    except Exception as error:
        return {"status": "error", "ocr_text": "", "lines": [], "message": str(error)}


def attach_analysis(data: dict) -> None:
    """Attach OCR evidence once per image in the current task."""
    for picture in data.get("images", []):
        if not picture.get("ocr_analysis"):
            picture["ocr_analysis"] = analyze_picture(picture)


def refresh_verification(data: dict) -> None:
    """Keep the visible image-model check in sync with OCR and user confirmation."""
    draft = data.get("draft") or {}
    planned = {item.get("source_id") for item in draft.get("image_plan", []) if isinstance(item, dict)}
    selected = [item for item in data.get("images", []) if item.get("source_id") in planned]
    if not selected:
        state, detail = "pending", "当前草稿没有选择图片，无法核验图片型号。"
    elif any((item.get("manual_confirmation") or {}).get("status") == "rejected" for item in selected):
        state, detail = "fail", "用户人工确认至少一张选中图片型号不一致。"
    elif all(
        (item.get("manual_confirmation") or {}).get("status") == "confirmed"
        or (
            (item.get("ocr_analysis") or {}).get("status") == "ok"
            and (item.get("ocr_analysis") or {}).get("ocr_text", "").strip()
        )
        for item in selected
    ):
        evidence = "用户人工确认" if all(
            (item.get("manual_confirmation") or {}).get("status") == "confirmed"
            for item in selected
        ) else "OCR 文本证据"
        state, detail = "pass", f"所有选中图片已获得{evidence}，未发现明确型号冲突；画面内容仍建议人工复核。"
    else:
        state, detail = "pending", "部分选中图片仍缺少 OCR 证据或人工确认。"

    for check in data.get("verification", []):
        if check.get("name") == "图片型号":
            check["state"] = state
            check["detail"] = detail
