"""Use downloaded project images; never load model-provided URLs or paths."""

from io import BytesIO
from hashlib import sha256
from pathlib import Path
import re
import warnings
from urllib.parse import quote
from zipfile import ZipFile, ZIP_STORED

from PIL import Image

from config import OUTPUTS, PLATFORMS, ROOT
from product_identity import different_model


def _read_image(picture: dict) -> tuple[Path, bytes]:
    value = picture.get("path")
    if not isinstance(value, str) or not value:
        raise ValueError("配图文件缺失，请重新读取资料。")
    path = (ROOT / value).resolve()
    if not path.is_relative_to(OUTPUTS.resolve()) or not path.is_file():
        raise ValueError("配图文件缺失或位置不可用，请重新读取资料。")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("配图超过8 MiB，请重新读取资料。")
    with path.open("rb") as stream:
        content = stream.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("配图超过8 MiB，请重新读取资料。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as picture_file:
                if picture_file.format not in ("PNG", "JPEG"):
                    raise ValueError("Unsupported image format")
                picture_file.verify()
            with Image.open(BytesIO(content)) as picture_file:
                picture_file.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("配图已损坏或不是可完整读取的 PNG / JPEG 文件，请重新读取资料。") from None
    return path, content


def image_file(picture: dict) -> Path:
    return _read_image(picture)[0]


def usable_images(data: dict) -> tuple[list[dict], list[str]]:
    """Count distinct, decodable files; titles do not prove visual identity."""
    usable, issues, seen, identifiers = [], [], set(), set()
    for picture in data["images"]:
        label = picture["source_id"] + " · " + picture["title"]
        if picture["source_id"] in identifiers:
            issues.append(label + "：配图编号重复，请重新读取资料。")
            continue
        identifiers.add(picture["source_id"])
        if different_model(data["task"]["product"], picture["title"]):
            issues.append(label + "：标题指向其他型号，不能用于本次任务。")
            continue
        try:
            _, content = _read_image(picture)
        except (ValueError, OSError) as error:
            detail = str(error) if isinstance(error, ValueError) else "配图文件读取失败，请重新读取资料。"
            issues.append(label + "：" + detail)
            continue
        digest = sha256(content).digest()
        if digest in seen:
            issues.append(label + "：与其他配图是同一份图片，不能重复凑图数。")
            continue
        seen.add(digest)
        usable.append(picture)
    return usable, issues


def delivery_issues(data: dict, draft: dict | None = None) -> list[str]:
    """The same delivery gate applies to generation, display and export."""
    draft = draft if draft is not None else data.get("draft")
    if not draft:
        return []
    minimum, maximum = PLATFORMS[data["task"]["platform"]]["images"]
    plans = draft.get("image_plan", [])
    identifiers = [item.get("source_id") for item in plans]
    if any(not isinstance(item, str) for item in identifiers) or len(set(identifiers)) != len(identifiers):
        return ["配图方案编号缺失或重复，不能交付图文。"]
    issues = []
    if not minimum <= len(identifiers) <= maximum:
        issues.append(f"{data['task']['platform']}要求{minimum}–{maximum}张实际图片，当前方案为{len(identifiers)}张。")
    markers = re.findall(r"【配图\s*(I\d+)\s*】", draft["body"])
    if sorted(markers) != sorted(identifiers):
        issues.append("正文插图与配图方案未一一对应，不能交付图文。")
    chosen = []
    for identifier in identifiers:
        matches = [picture for picture in data["images"] if picture["source_id"] == identifier]
        if len(matches) != 1:
            issues.append(f"配图 {identifier} 没有唯一对应的来源图片，请重新读取资料。")
        else:
            chosen.append(matches[0])
        if chosen and chosen[-1].get("manual_confirmation", {}).get("status") == "rejected":
            issues.append(f"配图 {identifier}：用户人工确认图片型号不一致，不能交付。")
    _, file_issues = usable_images({"task": data["task"], "images": chosen})
    return issues + file_issues


def markdown_image(picture: dict, href: str | None = None) -> str:
    path = image_file(picture)
    title = picture["title"].replace("[", "（").replace("]", "）").replace("\n", " ")
    target = quote(href if href is not None else path.relative_to(ROOT).as_posix(), safe="/-_.")
    return f"![{title}](<{target}>)"


def bundle(result: dict) -> bytes:
    from agent import export_markdown

    data = result["data"]
    draft = data.get("draft")
    issues = delivery_issues(data)
    if issues:
        raise ValueError("配图检查未通过：" + "；".join(issues))
    planned = {item["source_id"] for item in draft["image_plan"]} if draft else set()
    chosen = [picture for picture in data["images"] if picture["source_id"] in planned]
    if not draft or not chosen:
        raise ValueError("生成带配图的草稿后，才能下载图文和原图。")
    files, links = [], {}
    for index, picture in enumerate(chosen, 1):
        try:
            _, content = _read_image(picture)
        except (ValueError, OSError):
            raise ValueError("选用的配图文件不可用，请重新读取资料后下载；未生成缺图压缩包。") from None
        suffix = ".png" if content.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"
        name = f"images/image_{index}{suffix}"
        links[picture["source_id"]] = name
        files.append((name, content))
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        archive.writestr("营销创作记录.md", export_markdown(result, image_links=links).encode("utf-8"))
        for name, content in files:
            archive.writestr(name, content)
    return output.getvalue()
