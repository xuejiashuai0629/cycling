"""Read-only ima access through the unmodified official CLI."""

from collections import deque
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from config import (
    API_TIMEOUT, DOWNLOAD_TIMEOUT, KNOWLEDGE_BASE, MAX_API_CALLS,
    OUTPUTS, ROOT, TOTAL_TIMEOUT, ima_environment, redact,
)
from product_identity import matches_product, model_names


class KnowledgeError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        # This is the official daily media-read quota response, not a missing file.
        self.quota_exhausted = "资料获取次数已达上限" in message


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower().replace("huawei", "").replace("华为", "")
    return re.sub(r"[\W_]+", "", value)


def relevant(product: str, title: str) -> bool:
    return matches_product(product, title)


def items(data: dict) -> list[dict]:
    value = data.get("info_list", data.get("knowledge_list", []))
    if not isinstance(value, list):
        raise KnowledgeError("ima 返回的资料列表格式异常，请稍后重试。")
    return [item for item in value if isinstance(item, dict)]


class ImaClient:
    def __init__(self, total_timeout: float = TOTAL_TIMEOUT):
        self.environment = ima_environment()
        self.deadline = time.monotonic() + total_timeout
        self.calls = 0
        self.limited = False

    def remaining(self, cap: float) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise KnowledgeError("本次读取已达到等待上限，请缩小到具体型号后重试。")
        return min(cap, remaining)

    def call(self, operation: str, body: dict) -> dict:
        if self.calls >= MAX_API_CALLS:
            self.limited = True
            raise KnowledgeError("本次已达到资料查询次数上限，请缩小型号范围后重试。")
        self.calls += 1
        try:
            completed = subprocess.run(
                [shutil.which("node") or "node", str(ROOT / "ima-skill" / "ima_api.cjs"),
                 "openapi/wiki/v1/" + operation, json.dumps(body, ensure_ascii=False)],
                cwd=ROOT, env=self.environment, capture_output=True, text=True,
                encoding="utf-8", timeout=self.remaining(API_TIMEOUT),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise KnowledgeError("ima 读取超时，读取进程已停止。请稍后重新提交。") from None
        except OSError:
            raise KnowledgeError("无法启动官方 ima 读取脚本，请检查 Node.js 和完整工具目录。") from None
        if completed.returncode:
            try:
                error = json.loads(completed.stderr)
            except (ValueError, TypeError):
                error = {}
            if error.get("code") == -200:
                raise KnowledgeError("官方 ima Skill 提示需要更新；本次请求未执行，请先确认官方更新。")
            raise KnowledgeError(redact(str(error.get("msg") or "官方 ima 脚本运行失败，请检查网络和凭证。")))
        try:
            response = json.loads(completed.stdout)
        except ValueError:
            raise KnowledgeError("ima 返回的内容无法解析，请稍后重试。") from None
        if not isinstance(response, dict) or response.get("code") != 0:
            message = response.get("msg", "ima 返回业务错误。") if isinstance(response, dict) else "ima 返回格式异常。"
            raise KnowledgeError(redact(str(message)))
        if not isinstance(response.get("data"), dict):
            raise KnowledgeError("ima 未返回可读取的数据。")
        return response["data"]

    def pages(self, operation: str, body: dict, max_pages: int = 3) -> list[dict]:
        collected = []
        cursor = ""
        seen = set()
        for _ in range(max_pages):
            data = self.call(operation, {**body, "cursor": cursor})
            collected.extend(items(data))
            next_cursor = data.get("next_cursor")
            if data.get("is_end") or not next_cursor or next_cursor == cursor or next_cursor in seen:
                return collected
            seen.add(next_cursor)
            cursor = next_cursor
        self.limited = True
        return collected

    def knowledge_base(self) -> str:
        matches = self.pages("search_knowledge_base", {"query": KNOWLEDGE_BASE, "limit": 20})
        exact = [item for item in matches if item.get("kb_name", item.get("name")) == KNOWLEDGE_BASE]
        if len(exact) != 1:
            raise KnowledgeError("未能唯一找到指定共享知识库，请检查当前凭证所属账号是否已加入该库。")
        identifier = exact[0].get("kb_id", exact[0].get("id"))
        if not identifier:
            raise KnowledgeError("指定知识库缺少访问标识，请检查官方接口响应。")
        return identifier

    def candidates(self, kb_id: str, product: str) -> list[dict]:
        found = self.pages("search_knowledge", {"knowledge_base_id": kb_id, "query": product})
        # Search may omit cursor fields. Empty or unusable search must browse directories.
        selected = [item for item in found if item.get("media_type") != 99 and relevant(product, item.get("title", ""))]
        matching_folders = [item for item in found if item.get("media_type") == 99 and relevant(product, item.get("title", ""))]
        queue = deque((folder["media_id"], [folder.get("title", "")], 1) for folder in matching_folders[:2])
        if not selected:
            queue.appendleft((None, [], 0))
        visited = set()
        browsed = 0
        while queue and browsed < 8 and self.calls < MAX_API_CALLS - 8:
            folder_id, context, depth = queue.popleft()
            if folder_id in visited:
                continue
            visited.add(folder_id)
            browsed += 1
            body = {"knowledge_base_id": kb_id, "limit": 50}
            if folder_id:
                body["folder_id"] = folder_id
            children = self.pages("get_knowledge_list", body, max_pages=2)
            for child in children:
                title = child.get("title", "")
                if child.get("media_type") == 99:
                    # Do not descend into a different product's folder.
                    related_folder = relevant(product, title) or (not model_names(title) and normalized(title) in normalized(product))
                    generic_folder = "matebook" not in normalized(title)
                    if depth < 4 and (related_folder or generic_folder):
                        entry = (child.get("media_id"), context + [title], depth + 1)
                        if related_folder:
                            queue.appendleft(entry)
                        else:
                            queue.append(entry)
                elif relevant(product, title) or ("matebook" not in normalized(title) and relevant(product, "/".join(context))):
                    selected.append({**child, "folder_title": context[-1] if context else "", "folder_path": "/".join(context)})
        if queue:
            self.limited = True
        unique = {}
        for item in selected:
            if item.get("media_id") and (item["media_id"] not in unique or item.get("folder_path")):
                unique[item["media_id"]] = item
        return list(unique.values())

    def read_media(self, item: dict) -> bytes:
        if item.get("media_type") == 99:
            raise KnowledgeError("文件夹不能作为资料原文读取。")
        data = self.call("get_media_info", {"media_id": item["media_id"]})
        url_info = data.get("url_info") or {}
        if not url_info.get("url"):
            raise KnowledgeError("该资料暂无可读取原文，请使用 ima 客户端查看原文。")
        try:
            downloaded = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--download"],
                input=json.dumps(url_info).encode("utf-8"), capture_output=True,
                cwd=ROOT, timeout=self.remaining(DOWNLOAD_TIMEOUT),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise KnowledgeError("资料下载超时，下载进程已停止。请稍后重试。") from None
        if downloaded.returncode:
            raise KnowledgeError("资料下载失败或文件过大，请使用 ima 客户端查看原文。")
        return downloaded.stdout


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward temporary access headers to an unexpected host.
        return None


def download_worker() -> None:
    try:
        info = json.loads(sys.stdin.buffer.read())
        url = info["url"]
        if urlsplit(url).scheme != "https":
            raise ValueError("HTTPS required")
        headers = info.get("headers") or {}
        request = Request(url, headers=headers)
        with build_opener(NoRedirect()).open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            if response.status != 200:
                raise ValueError("Non-200 response")
            content = response.read(8 * 1024 * 1024 + 1)
        if not content or len(content) > 8 * 1024 * 1024:
            raise ValueError("Invalid media size")
        sys.stdout.buffer.write(content)
    except Exception:
        sys.exit(1)


def collect_sources(product: str, request_id: str, image_count: int) -> dict:
    client = ImaClient()
    kb_id = client.knowledge_base()
    candidates = client.candidates(kb_id, product)
    documents = [item for item in candidates if item.get("media_type") == 7 or Path(item.get("title", "")).suffix.lower() in (".md", ".txt")]
    pictures = [item for item in candidates if item.get("media_type") == 9]
    documents.sort(key=lambda item: ("参数" not in item.get("title", ""), len(item.get("title", ""))))
    sources, images, warnings = [], [], []
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id):
        raise KnowledgeError("本次任务编号格式异常。")
    directory = OUTPUTS / request_id
    if not directory.resolve().is_relative_to(ROOT) or not directory.resolve().is_relative_to(OUTPUTS.resolve()):
        raise KnowledgeError("素材保存位置超出项目范围。")
    for item in documents[:3] + pictures[:image_count]:
        try:
            content = client.read_media(item)
            base = {"title": item.get("title", "未命名资料"), "knowledge_base": KNOWLEDGE_BASE, "media_id": item["media_id"], "knowledge_path": item.get("folder_path", ""), "path_note": "仅列出实际浏览到的目录层级，未取得完整库内路径" if item.get("folder_path") else "本次搜索未返回库内目录路径；请按资料标题在 ima 中定位"}
            if item in documents:
                try:
                    text = content.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise KnowledgeError("文档不是 UTF-8 文本，暂不自动转码。") from None
                if not text.strip():
                    raise KnowledgeError("文档原文为空。")
                if len(text) > 64000:
                    warnings.append(base["title"] + "较长，本次仅保留前 64000 字符，不能视为全文核验。")
                sources.append({**base, "source_id": f"D{len(sources) + 1}", "text": text[:64000], "match_status": "待核验"})
            else:
                if content.startswith(b"\xff\xd8\xff"):
                    suffix = ".jpg"
                elif content.startswith(b"\x89PNG\r\n\x1a\n"):
                    suffix = ".png"
                else:
                    raise KnowledgeError("第一版仅预览真实 JPEG / PNG 图片，当前文件签名不符合。")
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"image_{len(images) + 1}{suffix}"
                path.write_bytes(content)
                images.append({**base, "source_id": f"I{len(images) + 1}", "path": path.relative_to(ROOT).as_posix(), "match_status": "待核验", "description": "候选素材，需核对图片中的具体型号与版本", "position": "文案生成后确定"})
        except (KnowledgeError, OSError) as error:
            if isinstance(error, KnowledgeError) and error.quota_exhausted:
                # Further documents and images share this quota; do not keep requesting.
                raise
            warnings.append(item.get("title", "资料") + "：" + (str(error) if isinstance(error, KnowledgeError) else "本地文件保存失败。"))
            if client.deadline <= time.monotonic() or client.calls >= MAX_API_CALLS:
                break
    if not sources:
        reason = "本次未读到该型号的可用文字资料，不能据此断言库里没有。"
        if warnings:
            reason += "具体原因：" + warnings[0]
        elif candidates:
            reason += f"本次找到 {len(candidates)} 条对应型号候选资料，但没有程序当前支持读取的 UTF-8 文字文件（.md / .txt）。"
        else:
            reason += "本次搜索及已浏览目录未找到对应型号的可读文字条目。请核对型号写法或稍后重试。"
        if client.limited:
            reason += "本次查询达到范围上限，尚未检查全部资料。"
        raise KnowledgeError(redact(reason))
    if len(images) < image_count:
        warnings.append(f"当前只读取到 {len(images)} 张候选图片，该平台至少需要 {image_count} 张，仍需补齐并核对。")
    if client.limited:
        warnings.append("本次浏览达到查询范围上限，尚未检查全部资料。")
    return {"sources": sources, "images": images, "warnings": warnings}


if __name__ == "__main__" and sys.argv[1:] == ["--download"]:
    download_worker()
