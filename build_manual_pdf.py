"""将 作品说明书.md 转换为 PDF（借助本机 Edge 无头打印）。

用法：python build_manual_pdf.py
输出：作品说明书.pdf（与 md 同目录，图片按相对路径嵌入）
"""

import subprocess
import sys
from pathlib import Path

import markdown

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


def main() -> int:
    text = MD_FILE.read_text(encoding="utf-8")
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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
