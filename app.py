"""Conversational Streamlit workspace; stages share one DeepSeek adapter."""

from copy import deepcopy
import sys

import streamlit as st

import agent
from config import KNOWLEDGE_BASE, STREAMLIT_VERSION, runtime_issues
import llm
import diagnostics
from image_assets import bundle, delivery_issues, image_file
from requirements_review import LABELS, REQUIRED

st.set_page_config(page_title="华为 PC · 营销创作台", page_icon="✦", layout="wide")
st.markdown("""<style>
.stApp {background:#f7f7f4;color:#22252b;}
.block-container {max-width:1440px;padding-top:2rem;padding-bottom:3rem;}
[data-testid="stSidebar"] {background:#eeefeb;}
h1 {letter-spacing:-.04em;font-weight:750!important;}
.eyebrow {font-size:12px;letter-spacing:.16em;color:#777e77;}
</style>""", unsafe_allow_html=True)

DEFAULTS = {"busy": False, "result": None, "error_message": "", "history": [], "dialog": None, "confirmed_task": None, "run_mode": "本地资料测试", "connection_status": None}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = deepcopy(value)


def reset_results() -> None:
    for key, value in DEFAULTS.items():
        if key != "run_mode":
            st.session_state[key] = deepcopy(value)
    st.session_state.pop("pending_action", None)


def clear_task() -> None:
    if not st.session_state.busy:
        reset_results()


def pause_generation() -> None:
    if st.session_state.result:
        st.session_state.result = agent.pause_generation(st.session_state.result)
    st.session_state.busy = False
    st.session_state.pop("pending_action", None)


def queue_action(action: str) -> None:
    if st.session_state.busy:
        return
    dialog = st.session_state.dialog
    if action == "chat":
        text = st.session_state.get("user_message", "").strip()
        if not text:
            return
    elif action == "materials":
        if not dialog or dialog["stage"] != "ready_for_confirmation":
            return
    elif action in ("generate", "continue_generation"):
        if not st.session_state.result or st.session_state.confirmed_task != st.session_state.result["data"]["task"]:
            return
    st.session_state.pending_action = {
        "action": action, "text": st.session_state.get("user_message", ""),
        "history": deepcopy(st.session_state.history), "dialog": deepcopy(dialog),
        "result": deepcopy(st.session_state.result), "mode": st.session_state.run_mode,
    }
    st.session_state.busy = True
    st.session_state.error_message = ""


with st.sidebar:
    st.markdown("### ✦ 营销创作台")
    st.caption("浙客松 · 华为产业进阶赛道")
    st.write("**资料来源**")
    st.radio("运行模式", ["本地资料测试", "真实资料"], key="run_mode", disabled=st.session_state.busy, on_change=reset_results)
    if st.session_state.run_mode == "本地资料测试":
        st.caption("本地目录：华为PC全板块深度知识库_v7")
    st.caption("真实知识库：")
    st.caption(KNOWLEDGE_BASE)
    st.caption("ima 凭证已填写，尚需实时验证。" if diagnostics.ima_configured() else "尚未填写完整 ima 凭证。")
    st.button("检查知识库连接", disabled=st.session_state.busy or st.session_state.run_mode != "真实资料", on_click=queue_action, args=("connection",), width="stretch")
    connection = st.session_state.connection_status
    if connection:
        (st.success if connection["status"] == "ok" else st.error)(connection["message"])
        st.caption("上次检查时间（UTC）：" + connection["checked_at"])
        if connection.get("response_summary"):
            with st.expander("接口返回摘要（已隐藏凭证和内部标识）"):
                st.json(connection["response_summary"])
                st.caption(connection["scope"])
    st.write("**工作流程**")
    st.caption("理解需求与查资料 → 写文案 → 独立审核")
    st.caption("三个角色共用 DeepSeek；图片内容核验仍待完成。")
    if llm.is_ready():
        st.caption("DeepSeek 密钥已填写，首次请求时验证。")
    else:
        st.info("请在项目 .env 中填写 DEEPSEEK_API_KEY，保存后再开始对话。")
    st.caption(f"Python {sys.version.split()[0]} · Streamlit {st.__version__}")

if st.__version__ != STREAMLIT_VERSION:
    st.error(f"项目约定 Streamlit {STREAMLIT_VERSION}，请统一版本后运行。")
    st.stop()
issues = runtime_issues()
if issues:
    for issue in issues:
        st.error(issue)
    st.stop()

st.markdown('<div class="eyebrow">HUAWEI PC / CONTENT WORKSPACE</div>', unsafe_allow_html=True)
st.title("聊清需求，再开始创作")
st.caption("场景自由描述。一次列齐缺失信息，补充次数不限；必需信息明确并经你确认后再查资料、创作。")
if st.session_state.run_mode == "本地资料测试":
    st.warning("当前为本地资料测试，未调用 ima 知识库。")
    st.caption("使用项目内的实际资料；需求理解、写作和审核仍会调用 DeepSeek。")

left, right = st.columns([4, 6], gap="large")
with left:
    st.subheader("01 / 说说你的需求")
    with st.container(height=450, border=True):
        if not st.session_state.history:
            with st.chat_message("assistant"):
                st.write("这次要介绍哪款华为 PC？你也可以直接说完整的内容需求。")
        for message in st.session_state.history:
            with st.chat_message(message["role"]):
                st.write(message["content"])
    dialog = st.session_state.dialog
    st.chat_input("在这里回答，或补充、修改你的要求", key="user_message", max_chars=6000, disabled=st.session_state.busy, on_submit=queue_action, args=("chat",))
    if dialog:
        if dialog["stage"] == "ready_for_confirmation":
            st.button("确认需求，查找资料与图片", type="primary", disabled=st.session_state.busy, on_click=queue_action, args=("materials",), width="stretch")
        else:
            st.caption("尚有必需信息待明确，补齐并确认后才能创作；不会自动用假设补全。")
        with st.expander("当前理解的需求清单"):
            states = {"clear": "已明确", "missing": "缺失", "ambiguous": "模糊"}
            for field in REQUIRED:
                item = dialog["requirements"][field]
                st.write(f"**{LABELS[field]} · {states[item['state']]}**")
                st.caption(item["value"] or "尚未提供")
    st.button("清空任务与记录", on_click=clear_task, disabled=st.session_state.busy, width="stretch")
    if st.session_state.error_message:
        st.error(st.session_state.error_message)
        if st.session_state.result and not st.session_state.result["data"].get("_rewrite"):
            st.caption("右侧保留的结果可能来自先前请求，请核对其中的任务摘要。")

with right:
    st.subheader("02 / 四部分创作结果")
    result = st.session_state.result
    if not result:
        st.info("先在左侧聊天。需求确认后，这里会展示场景判断、知识库依据、图文软文、核验结果。")
    else:
        data = result["data"]
        image_problems = delivery_issues(data)
        visible_draft = data.get("draft") if not image_problems else None
        task, strategy = data["task"], data["strategy"]
        st.caption(f"本份结果：{task['product']} · {task['platform']} · {task['audience']}")
        if data.get("material_mode") == "local":
            st.warning("当前结果来自本地文件测试，未调用 ima 知识库。")
        if st.session_state.confirmed_task != task:
            st.warning("这是先前任务的结果。请确认当前需求并重新读取资料，再生成文案。")
        tabs = st.tabs(["场景判断", "知识库依据", "图文软文", "核验结果"])
        with tabs[0]:
            st.write("**使用场景**")
            st.write(task["scenario"])
            st.write("**内容切入点**")
            st.write(visible_draft["angle"] if visible_draft else strategy["focus"])
            st.write("**内容目标**")
            st.write(task["goal"] or "未提供")
            st.write("**平台结构**")
            st.write(strategy["structure"])
            st.caption(strategy["tone"])
            minimum, maximum = strategy["images"]
            st.write(f"配图要求：{minimum}–{maximum} 张")
            st.caption("八类常见场景的内容重点作为模型参考，实际场景不受分类限制。")
        with tabs[1]:
            for source in data["sources"]:
                with st.expander(source["source_id"] + " · " + source["title"]):
                    st.caption("来源：" + source["knowledge_base"])
                    st.caption(agent.source_location(source))
                    st.text(source["text"][:4000])
                    if len(source["text"]) > 4000:
                        st.caption("页面仅预览前4000字符；发给模型的每份资料最多前16000字符。")
            for picture in data["images"]:
                try:
                    st.image(str(image_file(picture)), caption=picture["title"], width="stretch")
                except Exception:
                    st.warning("该图片暂时无法预览，请重新读取并核对来源文件。")
                st.caption("来源：" + picture["knowledge_base"] + " · 型号与版本待核验")
                st.caption(agent.source_location(picture))
            if not data["images"]:
                st.info("没有候选图片，后续需补齐平台要求的配图。")
        with tabs[2]:
            draft = visible_draft
            if draft:
                st.warning("以下是待核验草稿，尚未完成全部审核。")
                if data.get("stage") == "revision_required":
                    st.warning("审核指出待处理事项，请查看核验结果。")
                st.subheader(draft["title"])
                st.write(draft["intro"])
                for section in agent.body_sections(data):
                    if section["kind"] == "text":
                        if section["text"].strip():
                            st.markdown(section["text"])
                    else:
                        try:
                            st.image(str(image_file(section["picture"])), caption=section["caption"], width="stretch")
                        except Exception:
                            st.warning("正文配图文件不可用，请重新读取资料；未用占位图替代。")
                st.write(draft["cta"])
                if draft["tags"]:
                    st.caption(" ".join("#" + tag.strip().lstrip("#") for tag in draft["tags"]))
                with st.expander("复制图文（点击代码框右上角复制按钮）"):
                    st.caption("待核验草稿；发布前请处理核验结果中的事项。文字复制不会自动复制图片，请使用下方“下载图文与原图”保留实际图片。")
                    st.code(agent.draft_markdown(data), language="markdown", wrap_lines=True)
                try:
                    package = bundle(result)
                    st.download_button("下载图文与原图（ZIP）", data=package, file_name="营销图文与原图.zip", mime="application/zip", disabled=st.session_state.busy, width="stretch")
                    st.caption("解压后保留 Markdown 文件和 images 文件夹在一起，可按正文位置查看原图。")
                except (ValueError, OSError) as error:
                    st.warning(str(error))
            else:
                if image_problems:
                    st.error("配图检查未通过，请补充或重新读取真实图片后再交付图文。")
                    for detail in image_problems:
                        st.write("- " + detail)
                elif data.get("stage") in ("rewriting", "review_retry"):
                    st.info(f"正在自动修改与复核，已完成 {len(data.get('review_history', []))} 轮检查。通过后会在这里展示文案。")
                    for reason in agent.failure_reasons(data):
                        st.caption(reason)
                elif data.get("stage") == "generation_paused":
                    st.info("本次写作与审核已暂停，进度已保留。点击下方按钮可继续。")
                elif data.get("stage") in ("blocked", "format_blocked", "awaiting_materials"):
                    st.warning("需要先补充或确认资料，原因如下：")
                    for reason in agent.failure_reasons(data):
                        st.write("- " + reason)
                    st.caption("处理后点击左侧“确认需求，查找资料与图片”重新读取，再继续生成。")
                else:
                    st.info("素材准备后，可以调用写作角色生成草稿，再交给审核角色检查。")
            running_generation = data.get("stage") in ("rewriting", "review_retry") or (st.session_state.busy and st.session_state.get("pending_action", {}).get("action") in ("generate", "continue_generation"))
            if running_generation:
                st.button("暂停自动重写", on_click=pause_generation, width="stretch")
                st.caption("暂停会在当前请求结束后生效。每轮会继续调用模型。")
            resume = bool(data.get("_rewrite"))
            st.button("继续写作与审核" if resume else "生成文案并审核", disabled=st.session_state.busy or bool(image_problems) or data.get("stage") in ("mock", "awaiting_materials", "blocked") or st.session_state.confirmed_task != task or not llm.is_ready(), on_click=queue_action, args=("continue_generation" if resume else "generate",), width="stretch")
        with tabs[3]:
            labels = {"pass": "✅ 已检查", "pending": "待核验", "fail": "❌ 未通过"}
            if "rewrite_count" in data:
                st.caption(f"已退回修改 {data['rewrite_count']} 次；可改写问题会自动继续处理，通过后展示。缺资料和图片核验需要补充或确认。")
            for check in data["verification"]:
                st.write(f"**{check['name']} · {labels[check['state']]}**")
                st.caption(check["detail"])
            for warning in data.get("warnings", []) + data.get("review_issues", []):
                st.warning(warning)
            for detail in image_problems:
                st.error("真实配图 · 未通过：" + detail)
        st.download_button("导出当前记录（Markdown）", data=agent.export_markdown(result).encode("utf-8"), file_name="营销创作记录.md", mime="text/markdown", disabled=st.session_state.busy, width="stretch")

if st.session_state.busy and "pending_action" in st.session_state:
    pending = st.session_state.pending_action
    continue_automatically = False
    try:
        action = pending["action"]
        with st.spinner({"chat": "正在理解你的需求…", "materials": "正在查找资料与图片…", "generate": "正在写作文案并独立审核…", "continue_generation": "正在按问题清单修改并重新审核…", "connection": "正在验证知识库连接…"}[action]):
            if action == "connection":
                response = diagnostics.check_ima_connection()
            elif action == "chat":
                response = agent.chat(pending["text"], pending["history"], state=pending["dialog"])
            elif action == "materials":
                if pending["mode"] == "本地资料测试":
                    response = agent.prepare_materials(pending["dialog"]["task"], mode="local")
                else:
                    response = agent.prepare_materials(pending["dialog"]["task"])
            else:
                response = agent.generate_content(pending["result"], resume=action == "continue_generation")
        if action == "connection":
            st.session_state.connection_status = response
        elif response["status"] == "ok":
            if action == "chat":
                st.session_state.dialog = response["data"]
                st.session_state.confirmed_task = None
                st.session_state.history.append({"role": "user", "content": pending["text"]})
            else:
                st.session_state.result = response
                if action == "materials":
                    st.session_state.confirmed_task = deepcopy(pending["dialog"]["task"])
                elif response["data"].get("stage") in ("rewriting", "review_retry"):
                    continue_automatically = True
            if not continue_automatically:
                st.session_state.history.append({"role": "assistant", "content": response["message"]})
        else:
            st.session_state.error_message = response["message"]
            if action in ("generate", "continue_generation") and response.get("data", {}).get("sources"):
                st.session_state.result = response
    except Exception:
        st.session_state.error_message = "本次处理未完成，你可以重试；若反复失败，请检查模型配置和知识库连接。"
    finally:
        if continue_automatically:
            st.session_state.pending_action = {**pending, "action": "continue_generation", "result": deepcopy(st.session_state.result)}
        else:
            st.session_state.busy = False
            st.session_state.pop("pending_action", None)
    st.rerun()
