"""Explicit real three-role check using previously fetched ima evidence."""

from copy import deepcopy
import argparse
import json

import agent
from config import ROOT


def main():
    parser = argparse.ArgumentParser(description="复用本地真实资料，限轮验证模型；测试限额不影响页面自动重写。")
    parser.add_argument("--max-rounds", type=int, default=5)
    args = parser.parse_args()
    if args.max_rounds < 1:
        parser.error("--max-rounds 必须大于0")
    cache = ROOT / ".ima-state" / "framework_smoke.json"
    if not cache.is_file():
        print("缺少此前真实 ima 素材记录，请先运行 CheckIma。")
        return 1
    source = json.loads(cache.read_text(encoding="utf-8"))
    if source.get("status") != "ok" or source.get("data", {}).get("stage") != "materials_ready":
        print("此前素材记录不符合真实资料检查条件。")
        return 1
    # Real model calls only; no new ima requests or credential values in output.
    history = []
    first_text = "我想给 MateBook GT 14 写一篇内容。"
    first = agent.chat(first_text, history)
    if first["status"] != "ok":
        print(first["message"])
        return 1
    history = [{"role": "user", "content": first_text}, {"role": "assistant", "content": first["message"]}]
    complete_text = "写给大学生社团的视频剪辑同学，场景是在宿舍整理和剪辑活动视频，再把素材交给队友。发布在门店朋友圈，希望读者预约到店咨询适合剪辑需求的配置；不写价格和优惠，不伪造使用体验或测试结果。产品确认是 MateBook GT 14。"
    complete = agent.chat(complete_text, history, state=first["data"])
    if complete["status"] != "ok":
        print(complete["message"])
        return 1
    if complete["data"]["stage"] != "ready_for_confirmation":
        print("模型仍要求澄清，未进入写作：" + complete["message"])
        return 1
    task = complete["data"]["task"]
    if task["product"] != "MateBook GT 14" or task["platform"] != "门店朋友圈":
        print("模型提取的型号或平台与测试要求不符，未进入写作。")
        return 1
    prepared = deepcopy(source)
    prepared["data"].update(task=task, strategy=agent.strategy_for(task), draft=None)
    result = agent.generate_content(prepared)
    rounds = 1
    while result["status"] == "ok" and result["data"].get("stage") in ("rewriting", "review_retry"):
        print(result["message"], flush=True)
        print(json.dumps({"failures": agent.failure_reasons(result["data"])}, ensure_ascii=False), flush=True)
        candidate = result["data"].get("_rewrite", {}).get("previous_draft") or {}
        if candidate:
            import re
            print(json.dumps({"marker_tokens": re.findall(r"【[^】]*】", candidate.get("body", "")), "planned_images": [item.get("source_id") for item in candidate.get("image_plan", [])]}, ensure_ascii=False), flush=True)
        if rounds >= args.max_rounds:
            result = agent.pause_generation(result)
            result["message"] = f"已达到本次测试{args.max_rounds}轮限额，暂停验证；页面不设此限额。"
            break
        result = agent.generate_content(result, resume=True)
        rounds += 1
    directory = ROOT / "outputs" / "deepseek_smoke"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "创作记录.md").write_text(agent.export_markdown(result), encoding="utf-8")
    summary = {"status": result["status"], "message": result["message"], "first_stage": first["data"]["stage"], "first_question_fields": first["data"]["question_fields"], "confirmed_stage": complete["data"]["stage"], "stage": result["data"]["stage"], "draft_generated": result["data"]["draft"] is not None, "source_count": len(result["data"]["sources"]), "image_count": len(result["data"]["images"]), "checks": result["data"]["verification"], "issues": result["data"].get("review_issues", []), "reused_cached_ima": True}
    summary.update(rewrite_count=result["data"].get("rewrite_count", 0), review_rounds=len(result["data"].get("review_history", [])))
    (ROOT / ".ima-state" / "deepseek_smoke_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" and result["data"]["draft"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
