"""将 作品说明书.md 转换为 PDF（借助本机 Edge 无头打印）。

用法：python build_manual_pdf.py
输出：作品说明书.pdf（与 md 同目录，图片按相对路径嵌入）
"""

import subprocess
import sys
from pathlib import Path

import html
import re

ROOT = Path(__file__).resolve().parent
MD_FILE = ROOT / "作品说明书.md"
HTML_FILE = ROOT / "作品说明书.html"
PDF_FILE = ROOT / "作品说明书.pdf"

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body {
  font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
  font-size: 10.5pt; line-height: 1.7; color: #222;
}
h1 { font-size: 17pt; border-bottom: 2px solid #c7000b; padding-bottom: 6px; }
h2 { font-size: 13.5pt; margin-top: 1.4em; border-left: 4px solid #c7000b; padding-left: 8px; }
h3 { font-size: 11.5pt; }
blockquote {
  margin: 0.6em 0; padding: 4px 12px; color: #555;
  border-left: 3px solid #ccc; background: #f7f7f7;
}
code {
  font-family: Consolas, monospace; font-size: 9pt;
  background: #f0f0f0; padding: 1px 4px; border-radius: 3px;
}
img { max-width: 100%; display: block; margin: 8px auto; border: 1px solid #ddd; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #bbb; padding: 4px 8px; text-align: left; }
th { background: #f0f0f0; }
h1, h2, h3 { page-break-after: avoid; }
img, table, blockquote { page-break-inside: avoid; }
"""


def _inline(value: str) -> str:
    value = html.escape(value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    return value


def _table_to_html(lines: list[str]) -> str:
    header = [cell.strip() for cell in lines[0].strip().strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    parts = ["<table><thead><tr>"]
    parts.extend(f"<th>{_inline(cell)}</th>" for cell in header)
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        parts.extend(f"<td>{_inline(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def simple_markdown_to_html(text: str) -> str:
    """Tiny fallback converter for the project manual (headings, tables, lists, code)."""
    lines = text.splitlines()
    output = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("```"):
            index += 1
            code = []
            while index < len(lines) and lines[index].strip() != "```":
                code.append(lines[index])
                index += 1
            index += 1
            output.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
            continue
        if stripped == "---":
            output.append("<hr>")
            index += 1
            continue
        if stripped.startswith("### "):
            output.append(f"<h3>{_inline(stripped[4:])}</h3>")
            index += 1
            continue
        if stripped.startswith("## "):
            output.append(f"<h2>{_inline(stripped[3:])}</h2>")
            index += 1
            continue
        if stripped.startswith("# "):
            output.append(f"<h1>{_inline(stripped[2:])}</h1>")
            index += 1
            continue
        if stripped.startswith(">"):
            quote = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip()[1:].strip())
                index += 1
            output.append("<blockquote>" + "<br>".join(_inline(item) for item in quote) + "</blockquote>")
            continue
        if stripped.startswith("|"):
            table = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table.append(lines[index].strip())
                index += 1
            output.append(_table_to_html(table))
            continue
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while index < len(lines) and re.match(r"^\d+\.\s+", lines[index].strip()):
                items.append(_inline(re.sub(r"^\d+\.\s+", "", lines[index].strip())))
                index += 1
            output.append("<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>")
            continue
        if stripped.startswith("- "):
            items = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(_inline(lines[index].strip()[2:]))
                index += 1
            output.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            continue
        output.append(f"<p>{_inline(stripped)}</p>")
        index += 1
    return "\n".join(output)


def main() -> int:
    text = MD_FILE.read_text(encoding="utf-8")
    try:
        import markdown
    except ImportError:
        body = simple_markdown_to_html(text)
    else:
        body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    html = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>作品说明书</title><style>{CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
    HTML_FILE.write_text(html, encoding="utf-8")

    if not EDGE.exists():
        print(f"未找到 Edge：{EDGE}", file=sys.stderr)
        return 1

    cmd = [
        str(EDGE),
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--virtual-time-budget=20000",
        f"--print-to-pdf={PDF_FILE}",
        HTML_FILE.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if result.returncode != 0 or not PDF_FILE.exists():
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        print("PDF 生成失败", file=sys.stderr)
        return 1

    size_kb = PDF_FILE.stat().st_size // 1024
    print(f"已生成 {PDF_FILE.name}（约 {size_kb} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
