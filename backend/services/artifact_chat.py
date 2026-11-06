"""Plan explicit chat edits from the current user message, then use version tools.

The planner never sees artifact bodies, attachments, summaries or old commands.
It cannot supply new content, choose arbitrary IDs, or commit a version itself.
"""
import json
import re

from services.artifact_store import ArtifactError, read_artifact, restore_version, restore_slide_version
from services.artifact_revision import revise_artifact
from services.artifact_selection import bind_selection, revision_selection
from services.conversation_context import resolve_reference, _version_from_message
from services.llm_client import chat_with_diagnostics, LLM_MAX_OUTPUT_TOKENS

ACTION_WORDS = re.compile(
    r"改|换|增|加|删|去掉|补|润色|重写|续写|扩写|缩短|翻译|调整|优化|完善|重做|"
    r"精简|恢复|还原|回退|回滚|撤销|变成|变为|简洁|更清楚|"
    r"\b(?:edit|revise|rewrite|replace|restore|revert|undo|update|shorten|translate)\b", re.I,
)
PREVIOUS_VERSION = re.compile(r"上一(?:个)?(?:版|版本)|上版|撤销(?:刚才|刚刚|上次|最近)?的?修改")
PLANNER_PROMPT = """判断当前用户消息是否明确要求现在操作已保存的作品。只输出 JSON：
{"action":"answer|revise|restore|clarify","evidence":"原消息中直接请求操作的原句","target":"上一版或明确版本的原文片段"}
answer：普通提问、分析/引用原文、建议、讨论如何修改/恢复、假设、被引述的指令、要求只给方案、不实际修改。创作新内容或处理用户本条消息粘贴的新文本，也只回答，除非明确要求用它替换已保存作品。
revise：明确要求现在改写/增删/替换/润色当前作品，包括“能帮我把结尾改成…吗”这样的礼貌请求。完整原消息会作为修改要求，不要改写要求。
restore：明确要求现在恢复到历史版本或撤销上次修改，也包括只恢复某一页、其他页面保持当前内容。target 必须直接摘录用户说的版本，例如“第一版”“v2”“上一版”“撤销刚才的修改”。
clarify：确实想操作，但未说明怎么改/恢复哪版，或同时要求恢复再修改、操作多份作品。
只有明确操作请求才能返回 revise/restore。否定、引用、示例、条件句不能授权操作。不能从之前的对话或选中了作品推断同意。
evidence 必须是当前用户消息中直接请求操作的连续原文；answer 的 evidence/target 留空。"""


def classify_action(message, resource_ref=None):
    text = str(message or "").strip()
    if not resource_ref and not ACTION_WORDS.search(text):
        return {"action": "answer"}
    try:
        raw, diagnostics = chat_with_diagnostics(
            [{"role": "system", "content": PLANNER_PROMPT},
             {"role": "user", "content": json.dumps({"current_user_message": text}, ensure_ascii=False)}],
            temperature=0, max_tokens=LLM_MAX_OUTPUT_TOKENS, thinking="disabled",
        )
        if diagnostics.get("finish_reason") == "length":
            raise ValueError("truncated plan")
        raw = str(raw or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        plan = json.loads(raw)
        if not isinstance(plan, dict) or plan.get("action") not in {"answer", "revise", "restore", "clarify"}:
            raise ValueError("invalid action")
        if plan["action"] in {"revise", "restore"}:
            evidence = plan.get("evidence")
            if not isinstance(evidence, str) or len(evidence.strip()) < 2 or evidence not in text:
                raise ValueError("ungrounded action")
        return plan
    except Exception as exc:
        raise ArtifactError(502, "未能可靠判断本次是否要修改作品，未执行操作。请明确写出要修改的内容或恢复的版本。") from exc


def prepare_action(student_id, user_id, message, resource_ref=None):
    """Return None for ordinary chat; capture a version before the write call."""
    plan = classify_action(message, resource_ref)
    if plan["action"] == "answer":
        return None
    if plan["action"] == "clarify":
        return {"reply": "请明确说明要修改哪些内容，或要恢复到哪个版本；本次没有修改作品。"}
    # Current-message identities win; the added deictic only supplies a fallback
    # for commands like “把结尾改成开放式” after a previously referenced work.
    artifact = resolve_reference(student_id, user_id, message + "\n当前作品原文", resource_ref)
    if not artifact:
        return {"reply": "请先在右侧选择作品；聊天中的正文可以先“保存为作品”，再提出修改或恢复要求。本次没有修改作品。"}
    if plan["action"] == "revise":
        artifact = bind_selection(artifact, resource_ref, message)
        selection = revision_selection(artifact, message, artifact.get("selection"))
        if selection:
            artifact = {**artifact, "selection": selection}
    expected = artifact["current_version"]
    if resource_ref and int(resource_ref["resource_id"]) == artifact["id"]:
        expected = resource_ref.get("current_version") or resource_ref["version"]
    if expected != artifact["current_version"]:
        raise ArtifactError(409, "作品已有新版本，请重新选择最新版后再操作；本次未修改作品。")
    prepared = {"action": plan["action"], "artifact": artifact, "expected_version": expected,
                "instruction": message}
    if plan["action"] == "restore":
        if (resource_ref or {}).get("selection_source") == "preview":
            bind_selection(artifact, resource_ref, message)
        literal = plan.get("target", "")
        if not isinstance(literal, str) or not literal or literal not in message:
            return {"reply": "请说明恢复到哪一版，例如“恢复第一版”或“恢复上一版”；本次没有修改作品。"}
        if PREVIOUS_VERSION.fullmatch(literal.strip()):
            target = artifact["version"] - 1
        else:
            target = _version_from_message(literal)
            # The requested historical snapshot is the restore source, not the
            # version the user was viewing before the operation.
            viewed = (resource_ref["version"] if resource_ref
                      and int(resource_ref["resource_id"]) == artifact["id"]
                      else artifact["current_version"])
            artifact = read_artifact(artifact["id"], viewed, student_id=student_id, user_id=user_id)
            prepared["artifact"] = artifact
        if target is None:
            return {"reply": "请明确指定历史版本，例如“恢复第一版”或“恢复上一版”；本次没有修改作品。"}
        if target < 1:
            return {"reply": "当前查看的是第一版，没有上一版可以恢复；本次没有修改作品。"}
        # Validate existence and ownership before any mutation.
        read_artifact(artifact["id"], target, student_id=student_id, user_id=user_id)
        prepared["target_version"] = target
        if artifact["type"] == "ppt" and re.search(r'[页张]|\b(?:slide|page)s?\b', message, re.I):
            from services.ppt_slides import list_slides, requested_slide, restore_slide, WHOLE
            selected_number = None
            if ((resource_ref or {}).get("selection") and not WHOLE.search(message)
                    and (resource_ref.get("selection_source") != "preview"
                         or resource_ref["resource_id"] == artifact["id"])):
                viewed_artifact = read_artifact(artifact["id"], resource_ref["version"],
                                               student_id=student_id, user_id=user_id)
                bound = bind_selection(viewed_artifact, resource_ref, message)
                selected_number = bound.get("selection", {}).get("slide_number")
            try:
                number = requested_slide(message, selected_number)
                if number is not None:
                    # Restoration always retains the latest other pages, even
                    # when the user is previewing an older source version.
                    current = read_artifact(artifact["id"], student_id=student_id, user_id=user_id)
                    if current["version"] != expected:
                        raise ArtifactError(409, "作品已更新，请重新读取后恢复；本次未修改作品。")
                    source = read_artifact(artifact["id"], target, student_id=student_id, user_id=user_id)
                    content = restore_slide(current["content"], source["content"], number)
                    selection = list_slides(current["content"])[number - 1]["selection"]
                    prepared["artifact"] = {**current, "selection": selection}
                    prepared["slide_number"] = number
                    if content == current["content"]:
                        prepared["reply"] = f"第 {number} 页已与 v{target} 的内容一致，本次没有新增版本。"
            except ValueError as exc:
                raise ArtifactError(400, str(exc).replace("修改", "恢复")) from exc
        if not prepared.get("slide_number") and target == artifact["current_version"]:
            return {"reply": f"当前已经是 v{target}，无需恢复，本次没有新增版本。"}
    return prepared


def execute_action(plan, student_id, user_id, instruction=None):
    base = plan["artifact"]
    if plan["action"] == "revise":
        resource = revise_artifact(
            base["id"], student_id, user_id, base["version"], plan["expected_version"],
            instruction if instruction is not None else plan["instruction"],
            selection=base.get("selection"),
            **({"scope_instruction": plan["instruction"]} if base["type"] == "ppt" else {}),
        )
        reply = f"已修改《{resource['title']}》，基于 v{base['version']} 保存为 v{resource['version']}。右侧已更新，历史版本仍保留。"
        if base.get("selection"):
            reply = f"已修改《{resource['title']}》的所选段落，其他正文保持原样，基于 v{base['version']} 保存为 v{resource['version']}。右侧已更新。"
            if base["selection"].get("slide_number"):
                reply = f"已修改《{resource['title']}》第 {base['selection']['slide_number']} 页，其他页面和公共代码保持原样，基于 v{base['version']} 保存为 v{resource['version']}。右侧已更新。"
    else:
        if plan.get("slide_number"):
            resource = restore_slide_version(base["id"], plan["target_version"], plan["expected_version"],
                                             plan["slide_number"], student_id=student_id, user_id=user_id)
            reply = (f"已将《{resource['title']}》第 {plan['slide_number']} 页恢复为 v{plan['target_version']} 的原文，"
                     f"其他页面保持当前内容，并保存为新版本 v{resource['version']}。")
            if resource.get("unchanged"):
                reply = f"第 {plan['slide_number']} 页已与 v{plan['target_version']} 的内容一致，本次没有新增版本。"
        else:
            resource = restore_version(base["id"], plan["target_version"], plan["expected_version"],
                                       student_id=student_id, user_id=user_id)
            reply = f"已将《{resource['title']}》恢复为 v{plan['target_version']} 的内容，并保存为新版本 v{resource['version']}。其他历史版本仍保留。"
    payload = {"action": plan["action"], "resource": resource, "base_version": base["version"]}
    if plan["action"] == "restore":
        payload["restored_from"] = plan["target_version"]
        if plan.get("slide_number"):
            payload.update(scope="slide", slide_number=plan["slide_number"])
    elif base.get("selection"):
        payload["scope"] = "slide" if base["selection"].get("slide_number") else "selection"
        if base["selection"].get("slide_number"):
            payload["slide_number"] = base["selection"]["slide_number"]
    return reply, payload
