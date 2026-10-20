"""Explicit network check. Run with the agreed Python: -m tests.live_ima_smoke."""

import json
from pathlib import Path
import sys

import agent
from config import ROOT


def main():
    result = agent.prepare_materials({
        "product": "MateBook GT 14", "audience": "内容创作者", "scenario": "用剪映整理并剪辑社团活动的1080p短视频",
        "platform": "门店朋友圈", "goal": "验证真实图文资料读取", "extra": "",
    })
    if result["status"] != "ok":
        print(result["message"])
        return 1
    data = result["data"]
    summary = {
        "status": "ok", "documents": [item["title"] for item in data["sources"]],
        "images": [item["title"] for item in data["images"]], "warnings": data["warnings"],
        "draft_generated": data["draft"] is not None,
    }
    state = ROOT / ".ima-state"
    state.mkdir(exist_ok=True)
    (state / "framework_smoke.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if data["sources"] and data["images"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
