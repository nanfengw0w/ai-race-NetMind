import json
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from agents.profile_agent import extract_profile
from agents.knowledge_agent import (
    attach_resource_references,
    format_context,
    retrieve_knowledge,
    retrieve_resource_context,
)
from agents.path_agent import generate_learning_path
from agents.assess_agent import generate_assessment_report
from agents.tutor_agent import build_followup_plan, plan_tutor_actions, reflect_resource, save_tutor_run
from agents.topic_utils import compact_resource_title, merge_topics, normalize_topic, topic_from_query

from agents.lecture_agent import generate_lecture
from agents.interactive_agent import generate_interactive, review_interactive_resource
from agents.quiz_agent import generate_quiz
from agents.mindmap_agent import generate_mindmap
from agents.ppt_agent import generate_ppt, review_ppt_resource
from agents.lab_agent import SYSTEM_PROMPT as LAB_SYSTEM_PROMPT, generate_lab
from services.llm_client import LLM_MAX_OUTPUT_TOKENS, chat as llm_chat, chat_stream
from services.app_settings import get_knowledge_base_mode
from services.custom_kb import get_custom_knowledge_stats
from services.chat_attachments import get_attachment_context
from services.long_context import prepare_model_input
from services.usage_tracker import ensure_user, usage_scope
from services.user_memory import get_active_memory_text
from services.explanation_bundle import review_lecture_resource
from db.database import get_db
from services.artifact_store import ArtifactError, create_artifact, append_revision, read_artifact
from services.conversation_context import build_context, resolve_reference, record_reference
from services.artifact_chat import prepare_action, execute_action
from services.artifact_selection import bind_selection
from services.understanding_chat import is_check_request, resolve_check_source


def _review_resource(resource: dict, action: dict) -> dict:
    if resource.get('type') == 'interactive':
        return review_interactive_resource(resource, action)
    if resource.get('type') == 'ppt' and (resource.get('metadata') or {}).get('format') == 'netmind-html-deck-v2':
        return review_ppt_resource(resource, action)
    if resource.get('type') == 'lecture' and 'explanation_bundle' in (resource.get('metadata') or {}):
        return review_lecture_resource(resource, action)
    return reflect_resource(resource, action)


def _ensure_student(student_id: str, user_id: str | None = None):
    db = get_db()
    owner_id = ensure_user(user_id) if user_id else None
    row = db.execute("SELECT id, owner_id FROM students WHERE id = ?", (student_id,)).fetchone()
    if not row:
        db.execute("INSERT INTO students (id, name, owner_id) VALUES (?, ?, ?)", (student_id, student_id, owner_id))
        db.commit()
    elif owner_id and not row["owner_id"]:
        db.execute("UPDATE students SET owner_id = ? WHERE id = ?", (owner_id, student_id))
        db.commit()
    elif owner_id and row["owner_id"] != owner_id:
        raise ArtifactError(403, "无权访问此学习会话")


def _save_conversation(student_id: str, role: str, content: str, artifact_action=None):
    db = get_db()
    cursor = db.execute(
        "INSERT INTO conversations (student_id, role, content, artifact_action_json) VALUES (?, ?, ?, ?)",
        (student_id, role, content, json.dumps(artifact_action, ensure_ascii=False) if artifact_action else None),
    )
    db.commit()
    return cursor.lastrowid


def _prepare_input(message: str, attachment_ids: list[str] | None = None) -> tuple[str, str, dict]:
    attachment_context = get_attachment_context(attachment_ids)
    prepared_message, diagnostics = prepare_model_input(
        message,
        attachment_context.get("text", ""),
        attachment_context.get("names", []),
    )
    display_message = str(message or "").strip()
    names = [name for name in attachment_context.get("names", []) if name]
    if names:
        display_message = (
            f"{display_message}\n\n"
            f"> Attachments: {', '.join(names)}"
        ).strip()
    return prepared_message, display_message, diagnostics


def _save_profile(student_id: str, profile: dict):
    db = get_db()
    existing = db.execute(
        "SELECT id, version FROM profiles WHERE student_id = ? ORDER BY version DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    version = (existing["version"] + 1) if existing else 1
    db.execute(
        "INSERT INTO profiles (student_id, profile_json, version) VALUES (?, ?, ?)",
        (student_id, json.dumps(profile, ensure_ascii=False), version),
    )
    db.commit()


def _get_latest_profile(student_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT profile_json FROM profiles WHERE student_id = ? ORDER BY version DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["profile_json"])
    except (TypeError, json.JSONDecodeError):
        return None


def _default_course() -> str:
    mode = get_knowledge_base_mode()
    if mode == "computer_network":
        return "计算机网络"
    if mode == "custom":
        stats = get_custom_knowledge_stats()
        file_names = stats.get("file_names") or []
        joined_names = " ".join(file_names).lower()
        domain_rules = [
            (("前端", "frontend", "react", "vue"), "前端开发"),
            (("数据库", "database", "sql"), "数据库"),
            (("人工智能", "机器学习", "ai"), "人工智能"),
            (("数据结构", "算法"), "数据结构与算法"),
            (("操作系统",), "操作系统"),
            (("软件工程",), "软件工程"),
        ]
        for keywords, label in domain_rules:
            if any(keyword in joined_names for keyword in keywords):
                return label
        if file_names:
            stem = re.sub(r"\.(txt|md|markdown|docx|pptx)$", "", file_names[0], flags=re.IGNORECASE)
            stem = re.sub(r"[_-]+", " ", stem).strip()
            if stem:
                return stem[:18]
        return "自定义课程"
    return "未指定课程"


def _merge_profile_hints(profile: dict, message: str) -> dict:
    merged = {**profile}
    weak_points = list(merged.get("薄弱知识点", []) or [])
    difficulty_text = re.sub(r'(?:不是|并非|没有)(?:不理解|不懂|卡住|没理解|不明白)', '', message or '')
    declared_difficulty = bool(re.search(r'不理解|不懂|卡住|没理解|不明白|理解不了|没学会', difficulty_text))
    if declared_difficulty:
        candidates = []
        topic_match = re.search(
            r"围绕\s*(.+?)(?:(?:，|,|\s)*(?:生成|制作|设计|做一份)|[，。,\n]|$)",
            message or "",
        )
        if topic_match:
            candidates.append(normalize_topic(topic_match.group(1).strip()))
        request_topic = topic_from_query(message)
        if request_topic and len(request_topic) >= 2 and request_topic not in {"知识库", "什么内容", "当前内容"}:
            candidates.append(request_topic)
        # A question is attention, not evidence of low ability. Preserve existing
        # profile entries and only append a learner's explicit difficulty report.
        for candidate in dict.fromkeys(candidates):
            if candidate and candidate not in weak_points:
                weak_points.append(candidate)

    if "计算机网络" in (message or ""):
        merged["当前课程"] = "计算机网络"
    elif get_knowledge_base_mode() != "computer_network" and merged.get("当前课程") in {
        "", "未知", "未指定课程", "计算机网络",
    }:
        merged["当前课程"] = _default_course()

    merged["薄弱知识点"] = weak_points
    return merged


def _fast_initial_profile(message: str) -> dict:
    default_course = _default_course()
    return _merge_profile_hints(
        {
            "专业方向": "未知",
            "当前课程": default_course,
            "知识基础": "medium",
            "薄弱知识点": [],
            "学习目标": "掌握当前知识点",
            "认知偏好": "图解与示例",
            "学习节奏": "normal",
            "易错点类型": [],
            "已有前置知识": [],
            "学习动机": "自主学习",
        },
        message,
    )


def _has_explicit_resource_request(message: str) -> bool:
    lowered = (message or "").lower()
    hints = [
        "生成", "制作", "做一份", "讲解", "练习", "题目", "思维导图",
        "课件", "ppt", "html", "实验", "实操", "案例", "评估", "报告", "路径",
        "图解", "逐步演示", "动画", "视频", "互动网页", "交互网页", "可交互",
    ]
    return any(hint in lowered for hint in hints)


def _get_resource_profile(
    student_id: str,
    message: str,
    history: list[dict] = None,
    prefer_fast_initial: bool = False,
    explicit_request: bool = False,
) -> tuple[dict, str]:
    cached = _get_latest_profile(student_id)
    if cached:
        return _merge_profile_hints(cached, message), "cache"
    if prefer_fast_initial and (explicit_request or _has_explicit_resource_request(message)):
        return _fast_initial_profile(message), "local"
    return _merge_profile_hints(extract_profile(message, history), message), "model"


def _save_resource(student_id: str, resource: dict, source_conversation_id: int | None = None) -> int:
    resource_type = resource.get("type", "")
    title = compact_resource_title(resource.get("title", ""), resource_type)
    resource["title"] = title
    stored = create_artifact(
        student_id=student_id, type=resource_type, title=title,
        content=resource.get("content", ""), metadata=resource.get("metadata", {}),
        source_conversation_id=source_conversation_id,
    )
    resource.update(stored)
    return stored["id"]



def _save_quiz_questions(student_id: str, resource_id: int, questions: list):
    db = get_db()
    saved_questions = []
    for q in questions:
        cursor = db.execute(
            "INSERT INTO quizzes (resource_id, student_id, question_json, answer_json) VALUES (?, ?, ?, ?)",
            (
                resource_id,
                student_id,
                json.dumps(q, ensure_ascii=False),
                json.dumps({
                    "answer": q.get("answer"),
                    "accepted_answers": q.get("accepted_answers", []),
                    "answer_keywords": q.get("answer_keywords", []),
                    "explanation": q.get("explanation"),
                }, ensure_ascii=False),
            ),
        )
        saved_questions.append({**q, "quiz_id": cursor.lastrowid})
    db.commit()
    return saved_questions


def _attach_quiz_ids(resource_id: int, resource: dict, questions: list[dict]):
    resource["metadata"] = {**resource.get("metadata", {}), "questions": questions}
    resource["content"] = json.dumps(questions, ensure_ascii=False, indent=2)
    current = read_artifact(resource_id)
    resource.update(append_revision(
        resource_id, resource["content"], metadata=resource["metadata"],
        base_version=current["version"], expected_version=current["current_version"],
        change_request="关联持久化练习题编号", student_id=current["student_id"],
    ))


def _get_recent_quiz_records(student_id: str, limit: int = 10) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT qr.user_answer, qr.is_correct, q.question_json "
        "FROM quiz_records qr JOIN quizzes q ON qr.quiz_id = q.id "
        "WHERE qr.student_id = ? ORDER BY qr.timestamp DESC LIMIT ?",
        (student_id, limit),
    ).fetchall()

    records = []
    for row in rows:
        question = json.loads(row["question_json"])
        records.append({
            "question": question.get("question", ""),
            "options": question.get("options", []),
            "user_answer": row["user_answer"],
            "is_correct": bool(row["is_correct"]),
        })
    return records


# ── 对话模式 ──────────────────────────────────────────

def _complete_understanding_check(student_id, user_id, message, resource_ref):
    from services.understanding_checks import create_check, attach_conversation
    artifact = resolve_check_source(student_id, user_id, message, resource_ref)
    check = create_check(student_id, user_id, artifact, message)
    reference = {"resource_id": artifact["id"], "version": artifact["version"],
                 "title": artifact["title"], "content_hash": artifact["content_hash"]}
    if artifact.get("selection"):
        reference["selection"] = artifact["selection"]
    user_message_id = _save_conversation(student_id, "user", message.strip())
    record_reference(user_message_id, reference)
    source = check["source"]
    scope = f"第 {source['slide_number']} 页" if source.get("slide_number") else "所选段落" if artifact.get("selection") else "当前作品"
    reply = f"根据《{artifact['title']}》v{artifact['version']} 的{scope}准备了理解检查。请先用自己的话作答，需要时可以查看提示。"
    conversation_id = _save_conversation(student_id, "assistant", reply)
    record_reference(conversation_id, reference)
    attach_conversation(check["id"], conversation_id)
    return {"mode": "chat", "reply": reply, "summary": reply, "conversation_id": conversation_id,
            "understanding_check": check,
            "input_diagnostics": {"context": {"compacted": False, "references": [reference]}}}


def _complete_chat_action(student_id, user_id, message, attachment_ids, plan):
    """Persist an operation's receipt only after the version tool succeeds."""
    prepared, display, diagnostics = _prepare_input(message, attachment_ids)
    base = plan.get("artifact")
    reference = ({"resource_id": base["id"], "version": base["version"],
                  "title": base["title"], "content_hash": base["content_hash"],
                  **({"selection": base["selection"]} if base.get("selection") else {})} if base else None)
    user_message_id = _save_conversation(student_id, "user", display)
    record_reference(user_message_id, reference)
    payload = None
    receipt = None
    if "reply" in plan:
        reply = plan["reply"]
    else:
        reply, payload = execute_action(plan, student_id, user_id, instruction=prepared)
        resource = payload["resource"]
        receipt = {key: value for key, value in payload.items() if key != "resource"}
        receipt.update(resource_id=resource["id"], version=resource["version"])
    conversation_id = _save_conversation(student_id, "assistant", reply, artifact_action=receipt)
    record_reference(conversation_id, ({"resource_id": receipt["resource_id"], "version": receipt["version"]}
                                      if receipt else reference))
    diagnostics["context"] = {"compacted": False, "references": [reference] if reference else []}
    result = {"mode": "chat", "reply": reply, "conversation_id": conversation_id,
              "input_diagnostics": diagnostics}
    if payload:
        result["artifact"] = payload
    return result


CHAT_SYSTEM_PROMPT = """你是 NetMind，帮助用户自主学习和探索。直接回应当前问题，结合相关资料与对话，按需要使用例子、类比或推导。
默认用中文和易读的 Markdown 回答，详略由问题和用户要求决定。"""


def _prepare_chat_request(
    student_id: str,
    message: str,
    history: list[dict] = None,
    user_id: str | None = None,
    attachment_ids: list[str] | None = None,
    resource_ref: dict | None = None,
) -> tuple[dict, list[dict], dict]:
    _ensure_student(student_id, user_id)
    artifact = bind_selection(resolve_reference(student_id, user_id, message, resource_ref), resource_ref, message, read_only=True)
    prepared_message, display_message, input_diagnostics = _prepare_input(message, attachment_ids)

    knowledge_results = retrieve_knowledge(message, top_k=3)
    context = format_context(knowledge_results)

    cached_profile = _get_latest_profile(student_id)
    profile = (
        _merge_profile_hints(cached_profile, message)
        if cached_profile
        else _fast_initial_profile(message)
    )
    _save_profile(student_id, profile)

    knowledge_mode = get_knowledge_base_mode()
    mode_instruction = {
        "computer_network": "当前选择的是计算机网络预设知识库，可以在相关问题中使用网络课程资料。",
        "custom": "当前使用用户上传的知识库，按相关性参考检索资料。",
        "blank": "当前没有知识库参考资料。",
    }.get(knowledge_mode, "")
    memory_text = get_active_memory_text(user_id)
    memory_instruction = (
        "\n\n用户保存的偏好与背景，仅供相关问题参考："
        "\n"
        f"{memory_text}"
        if memory_text
        else ""
    )
    messages = [{
        "role": "system",
        "content": f"{CHAT_SYSTEM_PROMPT}\n\n{mode_instruction}{memory_instruction}",
    }]

    if context and context != "未找到相关知识库内容。":
        messages.append({
            "role": "system",
            "content": f"相关参考资料：\n\n{context[:2000]}"
        })

    messages, context_info = build_context(student_id, messages, prepared_message, artifact)
    conversation_id = _save_conversation(student_id, "user", display_message)
    record_reference(conversation_id, context_info["references"][0] if context_info["references"] else None)
    input_diagnostics["context"] = context_info
    return profile, messages, input_diagnostics


def process_chat_message(
    student_id: str,
    message: str,
    history: list[dict] = None,
    user_id: str | None = None,
    attachment_ids: list[str] | None = None,
    resource_ref: dict | None = None,
    pdf_ref: dict | None = None,
) -> dict:
    """对话模式：直接用LLM回复，结合知识库上下文"""
    _ensure_student(student_id, user_id)
    if pdf_ref is not None:
        from services.pdf_documents import PDFDocumentError
        from services.pdf_chat import answer_pdf_question
        if resource_ref is not None:
            raise PDFDocumentError(400, "请只引用一个来源：文档选段或作品")
        return answer_pdf_question(message, student_id, user_id, pdf_ref, attachment_ids)
    if is_check_request(message):
        return _complete_understanding_check(student_id, user_id, message, resource_ref)
    action = prepare_action(student_id, user_id, message, resource_ref)
    if action is not None:
        return _complete_chat_action(student_id, user_id, message, attachment_ids, action)
    profile, messages, input_diagnostics = _prepare_chat_request(
        student_id,
        message,
        history,
        user_id,
        attachment_ids,
        resource_ref,
    )

    reply = llm_chat(messages, temperature=0.7)
    conversation_id = _save_conversation(student_id, "assistant", reply)

    return {
        "mode": "chat",
        "reply": reply,
        "profile": profile,
        "input_diagnostics": input_diagnostics,
        "conversation_id": conversation_id,
    }


def process_chat_message_stream(
    student_id: str,
    message: str,
    history: list[dict] = None,
    user_id: str | None = None,
    attachment_ids: list[str] | None = None,
    resource_ref: dict | None = None,
    pdf_ref: dict | None = None,
):
    """流式对话：准备上下文后，把模型增量原样推送给前端。"""
    started_at = time.perf_counter()
    yield _event("status", {
        "stage": "context",
        "message": "正在理解本次要求并读取作品信息...",
    })
    _ensure_student(student_id, user_id)
    if pdf_ref is not None:
        from services.pdf_documents import PDFDocumentError
        from services.pdf_chat import answer_pdf_question
        if resource_ref is not None:
            raise PDFDocumentError(400, "请只引用一个来源：文档选段或作品")
        yield _event("status", {"stage": "pdf", "message": "正在读取文档选段并核对出处…"})
        result = answer_pdf_question(message, student_id, user_id, pdf_ref, attachment_ids)
        yield _event("context", result.get("input_diagnostics", {}).get("context", {}))
        yield _event("done", result)
        return
    if is_check_request(message):
        yield _event("status", {"stage": "check", "message": "正在根据当前作品原文准备理解检查..."})
        result = _complete_understanding_check(student_id, user_id, message, resource_ref)
        yield _event("context", result["input_diagnostics"]["context"])
        yield _event("done", result)
        return
    action = prepare_action(student_id, user_id, message, resource_ref)
    if action is not None:
        yield _event("status", {
            "stage": "artifact",
            "message": ("正在生成作品新版本..." if action.get("action") == "revise" else
                        "正在恢复历史版本..." if action.get("action") == "restore" else "正在确认操作要求..."),
        })
        result = _complete_chat_action(student_id, user_id, message, attachment_ids, action)
        yield _event("context", result["input_diagnostics"]["context"])
        if result.get("artifact"):
            yield _event("artifact", result["artifact"])
        result["timings_ms"] = {"total": round((time.perf_counter() - started_at) * 1000)}
        yield _event("done", result)
        return
    profile, messages, input_diagnostics = _prepare_chat_request(
        student_id,
        message,
        history,
        user_id,
        attachment_ids,
        resource_ref,
    )
    if input_diagnostics.get("strategy") == "map_reduce":
        yield _event("status", {
            "stage": "long_input",
            "message": (
                f"超长输入已拆分为 {input_diagnostics.get('chunks', 0)} 段，"
                "正在基于问题汇总关键证据..."
            ),
            "diagnostics": input_diagnostics,
        })
    yield _event("context", input_diagnostics.get("context", {}))
    yield _event("profile", profile)
    yield _event("status", {
        "stage": "answer",
        "message": "正在组织回答...",
    })

    parts = []
    first_token_at = None
    for delta in chat_stream(messages, temperature=0.7):
        if not delta:
            continue
        if first_token_at is None:
            first_token_at = time.perf_counter()
        parts.append(delta)
        yield _event("delta", {"delta": delta})

    reply = "".join(parts)
    conversation_id = _save_conversation(student_id, "assistant", reply)
    yield _event("done", {
        "reply": reply,
        "conversation_id": conversation_id,
        "input_diagnostics": input_diagnostics,
        "timings_ms": {
            "first_token": round(((first_token_at or time.perf_counter()) - started_at) * 1000),
            "total": round((time.perf_counter() - started_at) * 1000),
        },
    })


# ── 资源生成模式 ──────────────────────────────────────

def _prepare_resource_context(student_id, user_id, message, prepared_message, resource_ref):
    artifact = bind_selection(resolve_reference(student_id, user_id, message, resource_ref), resource_ref, message, read_only=True)
    messages, info = build_context(
        student_id,
        [{"role": "system", "content": "根据用户本次明确要求制作资源；历史仅作参考，不追加未请求的任务。"}],
        prepared_message, artifact,
    )
    # Historical data stays separate from the current opt-in planner request.
    evidence = [item for item in messages[:-1] if item["role"] != "system"]
    context = ("对话与作品参考数据（历史摘要不代表逐字原文）：\n" +
               json.dumps(evidence, ensure_ascii=False)) if evidence else ""
    return context, info


def process_message(
    student_id: str,
    message: str,
    history: list[dict] = None,
    mode: str = "sequential",
    user_id: str | None = None,
    attachment_ids: list[str] | None = None,
    required_intents: list[str] | None = None,
    resource_ref: dict | None = None,
) -> dict:
    """资源生成模式：生成学习资源"""
    _ensure_student(student_id, user_id)
    if is_check_request(message) and not required_intents:
        return _complete_understanding_check(student_id, user_id, message, resource_ref)
    model_message, display_message, input_diagnostics = _prepare_input(message, attachment_ids)
    conversation_context, context_info = _prepare_resource_context(
        student_id, user_id, message, model_message, resource_ref,
    )
    source_message_id = _save_conversation(student_id, "user", display_message)
    record_reference(source_message_id, context_info["references"][0] if context_info["references"] else None)
    input_diagnostics["context"] = context_info

    profile, _ = _get_resource_profile(student_id, model_message, history, prefer_fast_initial=True,
                                      explicit_request=bool({'interactive', 'ppt'}.intersection(required_intents or [])))
    _save_profile(student_id, profile)

    tutor_plan = plan_tutor_actions(
        student_id,
        model_message,
        profile,
        user_id,
        required_tools=required_intents,
    )
    intents = tutor_plan.intents
    resources = []

    explanation = _quick_stream_explanation(message, intents)
    reflections = []
    for action in tutor_plan.actions:
        intent = action["tool"]
        tool_message = model_message if intent in {'interactive', 'ppt'} else f"{model_message}\n\nTutor Agent 本轮要求：{action['instruction']}"
        query = " ".join(profile.get("薄弱知识点", [])) + " " + tool_message
        knowledge = retrieve_resource_context(intent, query, profile)
        context = "\n\n".join(part for part in [knowledge["context"], conversation_context] if part)
        res = _generate_fast_stream_resource_with_fallback(intent, tool_message, profile, context, student_id)
        if res:
            attach_resource_references(res, knowledge)
            review = _review_resource(res, action)
            if not review["passed"] and review.get('decision') != 'keep_text':
                repaired = _repair_resource_once(intent, tool_message, profile, context, student_id, review["issues"])
                if repaired:
                    res = repaired
                    attach_resource_references(res, knowledge)
                    review = {**_review_resource(res, action), "repaired": True}
            reflections.append(review)
            resources.append(res)

    for res in resources:
        if context_info["references"]:
            res["metadata"] = {**res.get("metadata", {}), "source_artifacts": context_info["references"]}
        rid = _save_resource(student_id, res, source_message_id)
        res["id"] = rid
        if res.get("type") == "quiz":
            questions = res.get("metadata", {}).get("questions", [])
            if questions:
                _attach_quiz_ids(rid, res, _save_quiz_questions(student_id, rid, questions))

    run_id = save_tutor_run(student_id, message, tutor_plan, reflections)

    assistant_msg = _resource_completion_summary(len(resources), len(intents))
    _save_conversation(student_id, "assistant", assistant_msg)

    return {
        "profile": profile,
        "resources": resources,
        "intents": intents,
        "mode": "resource",
        "explanation": explanation,
        "summary": assistant_msg,
        "resource_count": len(resources),
        "planned_count": len(intents),
        "failed_count": max(0, len(intents) - len(resources)),
        "tutor_plan": tutor_plan.to_dict(),
        "tutor_run_id": run_id,
        "reflections": reflections,
        "input_diagnostics": input_diagnostics,
    }


def _load_resources_by_ids(resource_ids: list[int]) -> list[dict]:
    if not resource_ids:
        return []
    placeholders = ", ".join("?" for _ in resource_ids)
    rows = get_db().execute(
        f"SELECT id, type, title, content, metadata_json, created_at FROM resources "
        f"WHERE id IN ({placeholders})",
        resource_ids,
    ).fetchall()
    resources_by_id = {
        row["id"]: {
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "content": row["content"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "status": "ready",
        }
        for row in rows
    }
    return [resources_by_id[rid] for rid in resource_ids if rid in resources_by_id]


def execute_tutor_followup(student_id: str, quiz_id: int, mastery: dict, user_id: str | None = None) -> dict:
    """Execute one bounded mastery-loop step and persist the generated resources."""
    _ensure_student(student_id, user_id)
    tutor_plan = build_followup_plan(mastery)
    action_name = mastery.get("action") or "retry"
    db = get_db()
    existing = db.execute(
        "SELECT resource_ids_json FROM tutor_followups "
        "WHERE student_id = ? AND quiz_id = ? AND action = ?",
        (student_id, quiz_id, action_name),
    ).fetchone()
    if existing:
        return {
            "plan": tutor_plan.to_dict(),
            "resources": _load_resources_by_ids(json.loads(existing["resource_ids_json"])),
            "reused": True,
        }

    if not tutor_plan.actions:
        return {"plan": tutor_plan.to_dict(), "resources": [], "reused": False}

    profile = _get_latest_profile(student_id) or {
        "当前课程": _default_course(),
        "知识基础": "medium",
        "薄弱知识点": [mastery.get("topic") or "当前知识点"],
    }
    topic = mastery.get("topic") or "当前知识点"

    def generate(action: dict) -> tuple[dict, dict] | None:
        with usage_scope(user_id, student_id):
            query = f"{topic} {action['instruction']}"
            knowledge = retrieve_resource_context(action["tool"], query, profile)
            context = knowledge["context"]
            resource = _generate_fast_stream_resource_with_fallback(
                action["tool"],
                f"Tutor Agent 自适应跟进：{action['instruction']}",
                profile,
                context,
                student_id,
            )
            if not resource:
                return None
            attach_resource_references(resource, knowledge)
            review = _review_resource(resource, action)
            if not review["passed"] and review.get('decision') != 'keep_text':
                repaired = _repair_resource_once(
                    action["tool"],
                    action["instruction"],
                    profile,
                    context,
                    student_id,
                    review["issues"],
                )
                if repaired:
                    resource = repaired
                    attach_resource_references(resource, knowledge)
                    review = {**_review_resource(resource, action), "repaired": True}
            return resource, review

    with ThreadPoolExecutor(max_workers=len(tutor_plan.actions)) as executor:
        generated = list(executor.map(generate, tutor_plan.actions))

    resources = []
    reflections = []
    resource_ids = []
    for item in generated:
        if not item:
            continue
        resource, review = item
        resource_id = _save_resource(student_id, resource)
        resource["id"] = resource_id
        resource["status"] = "ready"
        resource_ids.append(resource_id)
        if resource.get("type") == "quiz":
            questions = resource.get("metadata", {}).get("questions", [])
            if questions:
                _attach_quiz_ids(resource_id, resource, _save_quiz_questions(student_id, resource_id, questions))
        resources.append(resource)
        reflections.append(review)

    request = f"Tutor Agent 自适应跟进：{action_name} - {topic}"
    run_id = save_tutor_run(student_id, request, tutor_plan, reflections)
    db.execute(
        "INSERT INTO tutor_followups (student_id, quiz_id, action, resource_ids_json) VALUES (?, ?, ?, ?)",
        (student_id, quiz_id, action_name, json.dumps(resource_ids)),
    )
    db.commit()
    _save_conversation(student_id, "assistant", f"{tutor_plan.strategy}\n\n已生成 {len(resources)} 个自适应学习资源。")
    return {
        "plan": tutor_plan.to_dict(),
        "resources": resources,
        "reflections": reflections,
        "tutor_run_id": run_id,
        "reused": False,
    }


# ── 流式生成（用于SSE） ──────────────────────────────

AGENT_LABELS = {
    "profile": "理解问题",
    "planner": "讲解编排",
    "knowledge": "资料检索",
    "reflection": "资源质量反思",
    "lecture": "多形式讲解生成",
    "quiz": "练习题生成",
    "mindmap": "思维导图生成",
    "ppt": "PPT课件生成",
    "interactive": "互动教学生成",
    "lab": "实操案例生成",
    "path": "学习路径规划",
    "assessment": "评估报告生成",
}


RESOURCE_TYPE_BY_INTENT = {
    "lecture": "lecture",
    "quiz": "quiz",
    "mindmap": "mindmap",
    "ppt": "ppt",
    "interactive": "interactive",
    "lab": "lab",
    "path": "learning_path",
    "assessment": "assessment",
}

STREAMING_RESOURCE_INTENTS = {"lab"}


def _event(name: str, data: dict) -> dict:
    return {"event": name, "data": json.dumps(data, ensure_ascii=False)}


def _resource_completion_summary(completed: int, planned: int) -> str:
    if completed == 0:
        return "本次未能生成可用资源，请重新发送问题重试。"
    failed = max(0, planned - completed)
    if failed:
        return f"已生成 {completed} 个学习资源，另有 {failed} 个未完成，可重新发送对应问题重试。"
    return f"已完成 {completed} 个学习资源，可在右侧查看。"


def _log_timing(scope: str, payload: dict):
    print(f"[NetMind Timing][{scope}] {json.dumps(payload, ensure_ascii=False)}", flush=True)


def _quick_stream_explanation(message: str, intents: list[str]) -> str:
    names = [AGENT_LABELS.get(i, i).replace("生成", "").replace("规划", "") for i in intents]
    resource_text = "、".join(names) if names else "学习资源"
    return f"我会按「{resource_text}」逐项生成。每完成一个资源，都会立刻出现在右侧工作台，不用等全部结束。"


def _make_resource_placeholder(intent: str, index: int) -> dict:
    resource_type = RESOURCE_TYPE_BY_INTENT.get(intent, intent)
    label = AGENT_LABELS.get(intent, intent).replace("生成", "").replace("规划", "")
    return {
        "index": index,
        "intent": intent,
        "type": resource_type,
        "label": label,
        "title": f"{label}生成中",
        "status": "pending",
    }


def _get_existing_topics(student_id: str | None) -> list[str]:
    """从 mastery_states 获取该学生已有的知识点列表。"""
    if not student_id:
        return []
    try:
        rows = get_db().execute(
            "SELECT topic FROM mastery_states WHERE student_id = ? ORDER BY attempts DESC",
            (student_id,),
        ).fetchall()
        return [row["topic"] for row in rows]
    except Exception:
        return []


def _generate_resource_for_intent(
    intent: str,
    message: str,
    profile: dict,
    context: str,
    student_id: str | None = None,
) -> dict | None:
    if intent == "lecture":
        return generate_lecture(profile, context, message)
    if intent == 'interactive':
        return generate_interactive(profile, context, message)
    if intent == "quiz":
        existing = _get_existing_topics(student_id)
        return generate_quiz(profile, context, message, existing_topics=existing)
    if intent == "mindmap":
        return generate_mindmap(profile, context, message)
    if intent == "ppt":
        return generate_ppt(profile, context, message)
    if intent == "lab":
        return generate_lab(profile, context, message)
    if intent == "path":
        return generate_learning_path(profile)
    if intent == "assessment":
        records = _get_recent_quiz_records(student_id) if student_id else []
        return generate_assessment_report(profile, records, message)
    return None


def _repair_resource_once(
    intent: str,
    message: str,
    profile: dict,
    context: str,
    student_id: str | None,
    issues: list[str],
) -> dict | None:
    if intent in {'interactive', 'ppt'}:
        return None
    repair_message = (
        f"{message}\n\n"
        f"请修正以下问题并返回完整内容：{'；'.join(issues)}。"
    )
    return _generate_fast_stream_resource_with_fallback(intent, repair_message, profile, context, student_id)


def _generate_resource_with_fallback(
    intent: str,
    message: str,
    profile: dict,
    context: str,
    student_id: str | None = None,
) -> dict | None:
    if intent in {'lecture', 'interactive', 'ppt'}:
        # Raw HTML makes one request; lectures own their bounded repair.
        return _generate_resource_for_intent(intent, message, profile, context, student_id)
    try:
        return _generate_resource_for_intent(intent, message, profile, context, student_id)
    except Exception:
        fallback_map = {
            "quiz": lambda: generate_quiz(profile, context, message, existing_topics=_get_existing_topics(student_id)),
            "mindmap": lambda: generate_mindmap(profile, context, message),
            "lab": lambda: generate_lab(profile, context, message),
            "assessment": lambda: generate_assessment_report(
                profile,
                _get_recent_quiz_records(student_id) if student_id else [],
                message,
            ),
        }
        if intent in fallback_map:
            return fallback_map[intent]()
    return None


def _generate_fast_stream_resource_with_fallback(
    intent: str,
    message: str,
    profile: dict,
    context: str,
    student_id: str | None = None,
) -> dict | None:
    if intent in {'lecture', 'interactive', 'ppt'}:
        return _generate_resource_for_intent(intent, message, profile, context, student_id)
    try:
        return _generate_resource_for_intent(intent, message, profile, context, student_id)
    except Exception:
        return _generate_resource_with_fallback(intent, message, profile, context, student_id)


def _profile_summary(profile: dict) -> str:
    weak_points = profile.get("薄弱知识点", []) or []
    default_course = _default_course()
    return (
        f"课程：{profile.get('当前课程', default_course)}\n"
        f"专业方向：{profile.get('专业方向', '未知')}\n"
        f"知识基础：{profile.get('知识基础', 'medium')}\n"
        f"学习节奏：{profile.get('学习节奏', 'normal')}\n"
        f"认知偏好：{profile.get('认知偏好', '图解')}\n"
        f"薄弱知识点：{', '.join(weak_points) if weak_points else '暂未明确'}\n"
        f"学习目标：{profile.get('学习目标', '掌握课程内容')}"
    )


def _streaming_resource_config(intent: str, message: str, profile: dict, context: str) -> dict | None:
    default_course = _default_course()
    course = profile.get("当前课程", default_course)
    topic = topic_from_query(message, profile)
    related_topics = merge_topics(topic, profile.get("薄弱知识点", []) or [])
    profile_desc = _profile_summary(profile)

    configs = {
        "lab": {
            "type": "lab",
            "title": f"实操案例 - {topic or course}",
            "temperature": 0.7,
            "max_tokens": LLM_MAX_OUTPUT_TOKENS,
            "system": LAB_SYSTEM_PROMPT,
            "user": f"""学习背景：
{profile_desc}

知识库参考内容：
{context}

用户需求：
{message}""",
        },
    }

    config = configs.get(intent)
    if not config:
        return None

    config["metadata"] = {"related_topics": related_topics}
    return config


def _generate_streaming_resource(
    intent: str,
    message: str,
    profile: dict,
    context: str,
    emit_delta,
    student_id: str | None = None,
) -> dict:
    if intent == 'lecture':
        # The same implementation serves ordinary and SSE requests. Incomplete JSON
        # is never sent through resource_delta and rendered as a Markdown document.
        return generate_lecture(profile, context, message)
    if intent == 'interactive':
        return generate_interactive(profile, context, message)
    config = _streaming_resource_config(intent, message, profile, context)
    if not config:
        generated = _generate_resource_with_fallback(intent, message, profile, context, student_id)
        if not generated:
            raise RuntimeError(f"{intent} 生成失败")
        return generated

    messages = [
        {"role": "system", "content": config["system"]},
        {"role": "user", "content": config["user"]},
    ]

    parts = []
    try:
        for delta in chat_stream(
            messages,
            temperature=config["temperature"],
            max_tokens=config["max_tokens"],
        ):
            if not delta:
                continue
            parts.append(delta)
            emit_delta(delta)
    except Exception:
        if not parts:
            raise
        parts.append("\n\n> 生成过程中断，以上为已收到的内容。")

    return {
        "type": config["type"],
        "title": config["title"],
        "content": "".join(parts),
        "metadata": config["metadata"],
    }


def process_message_stream(
    student_id: str,
    message: str,
    history: list[dict] = None,
    mode: str = "sequential",
    user_id: str | None = None,
    attachment_ids: list[str] | None = None,
    required_intents: list[str] | None = None,
    resource_ref: dict | None = None,
):
    """流式处理学生消息：先推送资源计划，再逐个推送已完成资源。"""
    request_started = time.perf_counter()
    _ensure_student(student_id, user_id)
    if is_check_request(message) and not required_intents:
        yield _event("status", {"agent": "check", "message": "正在根据当前作品原文准备理解检查..."})
        result = _complete_understanding_check(student_id, user_id, message, resource_ref)
        yield _event("context", result["input_diagnostics"]["context"])
        yield _event("done", result)
        return
    model_message, display_message, input_diagnostics = _prepare_input(message, attachment_ids)
    conversation_context, context_info = _prepare_resource_context(
        student_id, user_id, message, model_message, resource_ref,
    )
    source_message_id = _save_conversation(student_id, "user", display_message)
    record_reference(source_message_id, context_info["references"][0] if context_info["references"] else None)
    input_diagnostics["context"] = context_info
    yield _event("context", context_info)
    if input_diagnostics.get("strategy") == "map_reduce":
        yield _event("status", {
            "agent": "context",
            "label": "长文本处理",
            "message": (
                f"已将超长输入拆成 {input_diagnostics.get('chunks', 0)} 段，"
                "并行提取与当前任务相关的内容。"
            ),
            "diagnostics": input_diagnostics,
        })

    profile_started = time.perf_counter()
    cached_profile = _get_latest_profile(student_id)
    yield _event("status", {
        "agent": "profile",
        "label": AGENT_LABELS["profile"],
        "message": "正在结合已有学习背景..." if cached_profile else "正在理解本次问题与学习背景...",
    })
    profile, profile_source = _get_resource_profile(student_id, model_message, history, prefer_fast_initial=True,
                                                   explicit_request=bool({'interactive', 'ppt'}.intersection(required_intents or [])))
    _save_profile(student_id, profile)
    yield _event("profile", {**profile, "_source": profile_source})
    profile_ms = round((time.perf_counter() - profile_started) * 1000)

    yield _event("status", {
        "agent": "planner",
        "label": AGENT_LABELS["planner"],
        "message": "正在结合当前问题和已有资料，选择需要制作的内容...",
    })
    planner_started = time.perf_counter()
    tutor_plan = plan_tutor_actions(
        student_id,
        model_message,
        profile,
        user_id,
        required_tools=required_intents,
    )
    planner_ms = round((time.perf_counter() - planner_started) * 1000)
    intents = tutor_plan.intents
    plan = [_make_resource_placeholder(intent, index) for index, intent in enumerate(intents)]
    explanation = _quick_stream_explanation(message, intents)
    yield _event("plan", {
        "intents": intents,
        "resources": plan,
        "explanation": explanation,
        "tutor_plan": tutor_plan.to_dict(),
    })
    yield _event("notice", {
        "message": explanation,
    })

    yield _event("status", {
        "agent": "knowledge",
        "label": AGENT_LABELS["knowledge"],
        "message": "正在为每种资源分别匹配参考资料...",
    })

    resources = []
    reflections = []
    resource_timings = {}
    event_queue = queue.Queue()
    resources_started = time.perf_counter()

    def emit_worker_event(event_name: str, payload: dict):
        event_queue.put(("event", _event(event_name, payload)))

    def generate_worker(index: int, intent: str):
        worker_started = time.perf_counter()
        action = tutor_plan.actions[index]
        tool_message = model_message if intent in {'interactive', 'ppt'} else f"{model_message}\n\nTutor Agent 本轮要求：{action['instruction']}"
        label = AGENT_LABELS.get(intent, intent)
        display_label = label.removesuffix("生成").removesuffix("规划")
        resource_type = RESOURCE_TYPE_BY_INTENT.get(intent, intent)
        timing = {
            "index": index,
            "intent": intent,
            "queue_wait": round((worker_started - resources_started) * 1000),
        }
        try:
            usage_context = usage_scope(user_id, student_id)
            usage_context.__enter__()
            knowledge_started = time.perf_counter()
            query = " ".join(profile.get("薄弱知识点", [])) + " " + tool_message
            knowledge = retrieve_resource_context(intent, query, profile)
            context = "\n\n".join(part for part in [knowledge["context"], conversation_context] if part)
            timing["knowledge"] = round((time.perf_counter() - knowledge_started) * 1000)
            timing["knowledge_chunks"] = knowledge["matched_chunks"]
            emit_worker_event("status", {
                "agent": intent,
                "index": index,
                "phase": "resource",
                "status": "current",
                "label": label,
                "message": ('正在统一生成讲解、图解与过程演示，并检查结构；完成后显示。' if intent == 'lecture'
                            else '正在生成完整互动教学，完成后直接显示。' if intent == 'interactive'
                            else f"正在生成{label}..."),
            })
            emit_worker_event("resource_start", {
                "index": index,
                "intent": intent,
                "type": resource_type,
                "label": label,
                "title": f"{label}中",
            })

            generation_started = time.perf_counter()
            if intent in STREAMING_RESOURCE_INTENTS:
                res = _generate_streaming_resource(
                    intent,
                    tool_message,
                    profile,
                    context,
                    lambda delta: emit_worker_event("resource_delta", {
                        "index": index,
                        "intent": intent,
                        "type": resource_type,
                        "delta": delta,
                    }),
                    student_id,
                )
            else:
                res = _generate_fast_stream_resource_with_fallback(intent, tool_message, profile, context, student_id)
            timing["generate"] = round((time.perf_counter() - generation_started) * 1000)

            if not res:
                emit_worker_event("resource_error", {
                    "index": index,
                    "intent": intent,
                    "type": resource_type,
                    "title": f"{display_label}生成未完成",
                    "message": f"{display_label}未能生成可用内容，请重新发送问题重试。",
                })
                return

            attach_resource_references(res, knowledge)
            reflection_started = time.perf_counter()
            review = _review_resource(res, action)
            timing["reflection"] = round((time.perf_counter() - reflection_started) * 1000)
            if not review["passed"] and review.get('decision') != 'keep_text':
                emit_worker_event("status", {
                    "agent": intent,
                    "index": index,
                    "phase": "resource",
                    "status": "current",
                    "label": label,
                    "message": f"{label}需要修复：{'；'.join(review['issues'])}",
                })
                repair_started = time.perf_counter()
                repaired = _repair_resource_once(
                    intent,
                    tool_message,
                    profile,
                    context,
                    student_id,
                    review["issues"],
                )
                timing["repair"] = round((time.perf_counter() - repair_started) * 1000)
                if repaired:
                    res = repaired
                    attach_resource_references(res, knowledge)
                    review = {
                        **_review_resource(res, action),
                        "repaired": True,
                    }

            timing["worker_total"] = round((time.perf_counter() - worker_started) * 1000)
            timing["resource_diagnostics"] = res.get("metadata", {}).get("generation_diagnostics")
            resource_timings[index] = timing
            event_queue.put(("resource_done", index, intent, res, review))
        except Exception as exc:
            emit_worker_event("resource_error", {
                "index": index,
                "intent": intent,
                "type": resource_type,
                "title": f"{display_label}生成未完成",
                "message": f"{display_label}未能生成可用内容，请重新发送问题重试。",
                "detail": str(exc)[:1600],
            })
        finally:
            if "usage_context" in locals():
                usage_context.__exit__(None, None, None)
            resource_timings.setdefault(index, timing)
            event_queue.put(("worker_done", index, intent))

    threads = [
        threading.Thread(target=generate_worker, args=(index, intent), daemon=True)
        for index, intent in enumerate(intents)
    ]
    for thread in threads:
        thread.start()

    finished_workers = 0
    while finished_workers < len(threads):
        item = event_queue.get()
        kind = item[0]

        if kind == "event":
            yield item[1]
        elif kind == "resource_done":
            _, index, intent, res, review = item
            save_started = time.perf_counter()
            if context_info["references"]:
                res["metadata"] = {**res.get("metadata", {}), "source_artifacts": context_info["references"]}
            rid = _save_resource(student_id, res, source_message_id)
            res["id"] = rid
            res["status"] = "ready"
            resources.append(res)

            if res.get("type") == "quiz":
                questions = res.get("metadata", {}).get("questions", [])
                if questions:
                    _attach_quiz_ids(rid, res, _save_quiz_questions(student_id, rid, questions))
            resource_timings.setdefault(index, {})["save"] = round((time.perf_counter() - save_started) * 1000)

            reflections.append(review)
            yield _event("reflection", {"index": index, "intent": intent, "review": review})
            yield _event("resource_timing", {"index": index, **resource_timings.get(index, {})})
            yield _event("resource", {"index": index, "intent": intent, "resource": res})
        elif kind == "worker_done":
            finished_workers += 1

    for thread in threads:
        thread.join(timeout=0.1)

    assistant_msg = _resource_completion_summary(len(resources), len(intents))
    conversation_save_started = time.perf_counter()
    _save_conversation(student_id, "assistant", assistant_msg)
    conversation_save_ms = round((time.perf_counter() - conversation_save_started) * 1000)
    tutor_run_save_started = time.perf_counter()
    run_id = save_tutor_run(student_id, message, tutor_plan, reflections)
    tutor_run_save_ms = round((time.perf_counter() - tutor_run_save_started) * 1000)

    timings_ms = {
        "profile": profile_ms,
        "planner": planner_ms,
        "knowledge": sum(item.get("knowledge", 0) for item in resource_timings.values()),
        "resources": round((time.perf_counter() - resources_started) * 1000),
        "conversation_save": conversation_save_ms,
        "tutor_run_save": tutor_run_save_ms,
        "total": round((time.perf_counter() - request_started) * 1000),
    }
    timing_payload = {
        "student_id": student_id,
        "timings_ms": timings_ms,
        "resource_timings_ms": [resource_timings[index] for index in sorted(resource_timings)],
    }
    _log_timing("resource_request", timing_payload)
    yield _event("done", {
        "intents": intents,
        "resource_count": len(resources),
        "planned_count": len(intents),
        "failed_count": max(0, len(intents) - len(resources)),
        "summary": assistant_msg,
        "explanation": explanation,
        "profile_source": profile_source,
        "tutor_plan": tutor_plan.to_dict(),
        "tutor_run_id": run_id,
        "reflections": reflections,
        "timings_ms": timings_ms,
        "resource_timings_ms": timing_payload["resource_timings_ms"],
        "input_diagnostics": input_diagnostics,
    })
