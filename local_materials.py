"""Explicit project-local test materials; never an automatic ima fallback."""

from pathlib import Path
import re

from config import OUTPUTS, ROOT
from product_identity import matches_product, model_names
from tools import KnowledgeError

LOCAL_DIRECTORY = ROOT / "华为PC全板块深度知识库_v7"
LOCAL_NOTICE = "当前为本地资料测试，未调用 ima 知识库。"
MAX_FILE_BYTES = 8 * 1024 * 1024


def collect_local_sources(product: str, request_id: str, image_count: int) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id):
        raise KnowledgeError("本地测试任务编号格式异常。")
    local_root = LOCAL_DIRECTORY.resolve()
    destination = (OUTPUTS / request_id).resolve()
    if not local_root.is_relative_to(ROOT) or not destination.is_relative_to(ROOT) or not destination.is_relative_to(OUTPUTS.resolve()):
        raise KnowledgeError("本地测试文件位置超出项目范围。")
    if not local_root.is_dir():
        raise KnowledgeError(LOCAL_NOTICE + "请先把本地资料目录放回项目文件夹。")
    candidates = []
    for path in local_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in (".md", ".txt", ".png", ".jpg", ".jpeg"):
            continue
        if not path.resolve().is_relative_to(local_root):
            continue
        context = path.parent.relative_to(local_root).as_posix()
        if matches_product(product, path.name) or (not model_names(path.name) and matches_product(product, context)):
            candidates.append(path)
    documents = sorted((p for p in candidates if p.suffix.lower() in (".md", ".txt")), key=lambda p: ("参数" not in p.name, len(p.name), p.as_posix()))
    pictures = sorted((p for p in candidates if p.suffix.lower() in (".png", ".jpg", ".jpeg")), key=lambda p: ("主视觉" not in p.name, p.as_posix()))
    sources, images, warnings = [], [], [LOCAL_NOTICE]
    for paths, target, limit in ((documents, sources, 3), (pictures, images, image_count)):
        for path in paths:
            if len(target) >= limit:
                break
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise KnowledgeError("文件超过 8 MiB，未读取。")
                content = path.read_bytes()
                base = {"title": path.name, "knowledge_base": "本地测试资料（未调用 ima）", "local_path": path.relative_to(ROOT).as_posix(), "match_status": "待核验"}
                if target is sources:
                    try:
                        text = content.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        raise KnowledgeError("文档不是 UTF-8 文本，未读取。") from None
                    if not text.strip():
                        raise KnowledgeError("文档原文为空。")
                    if len(text) > 64000:
                        warnings.append(path.name + "较长，本地测试仅保留前64000字符。")
                    sources.append({**base, "source_id": f"D{len(sources) + 1}", "text": text[:64000]})
                else:
                    if content.startswith(b"\xff\xd8\xff"):
                        suffix = ".jpg"
                    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
                        suffix = ".png"
                    else:
                        raise KnowledgeError("文件内容不是支持的 JPEG / PNG 图片。")
                    destination.mkdir(parents=True, exist_ok=True)
                    copied = destination / f"image_{len(images) + 1}{suffix}"
                    copied.write_bytes(content)
                    images.append({**base, "source_id": f"I{len(images) + 1}", "path": copied.relative_to(ROOT).as_posix(), "description": "本地候选图片，画面型号和版本待核验", "position": "文案生成后确定"})
            except (KnowledgeError, OSError) as error:
                warnings.append(path.name + "：" + (str(error) if isinstance(error, KnowledgeError) else "本地文件读取或保存失败。"))
    if not sources:
        detail = warnings[1] if len(warnings) > 1 else "本地目录未找到对应型号的可用文字文件。"
        raise KnowledgeError(LOCAL_NOTICE + detail)
    if len(images) < image_count:
        warnings.append(f"本地只有 {len(images)} 张可读取的候选图，本平台至少需要 {image_count} 张。")
    return {"sources": sources, "images": images, "warnings": warnings, "material_mode": "local", "source_notice": LOCAL_NOTICE}


def product_candidates() -> list[dict]:
    """List locally indexed models with their one-line positioning for A1.5."""
    root = LOCAL_DIRECTORY / "P1-产品参数库"
    result = []
    for path in root.glob("*/*/卖点与限制.md"):
        product = path.parent.name
        try:
            text = path.read_text(encoding="utf-8-sig")
            section = text.split("## 一句话定位", 1)[1]
            summary = next(line.strip() for line in section.splitlines() if line.strip())
        except (IndexError, StopIteration, OSError):
            summary = ""
        result.append({
            "product": product,
            "summary": summary,
            "source_path": path.relative_to(ROOT).as_posix(),
        })
    return sorted(result, key=lambda item: item["product"])
