import json
import asyncio
import queue
import re
import threading
import time
import unicodedata
import uuid
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from typing import Literal
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from services.chat_service import (
    execute_tutor_followup,
    process_chat_message,
    process_chat_message_stream,
    process_message,
    process_message_stream,
)
from services.profile_evidence import build_profile_evidence
from db.database import get_db
from services.artifact_store import read_artifact
from services.artifact_selection import ArtifactSelection
from agents.assess_agent import generate_assessment_report
from agents.topic_utils import normalize_topic
from agents.tutor_agent import get_mastery_state, update_mastery_from_quiz
from services.usage_tracker import usage_scope
from services.chat_attachments import create_attachment, delete_attachment
from bkt import BKTParams, apply_forgetting, compute_review_schedule
from services.user_space import (
    get_local_user_ids,
    get_owned_session_ids,
    get_user_space,
    record_learning_activity,
    record_learning_event,
)

router = APIRouter(prefix="/api", tags=["chat"])


def _action_priority(action: str | None) -> int:
    return {
        "remediate": 0,
        "retry": 1,
        "reinforce": 2,
        "advance": 3,
    }.get(action or "", 4)


def _merge_mastery_rows(rows) -> list[dict]:
    """Normalize historical fine-grained topics before returning BKT data."""
    merged: dict[str, dict] = {}
    for row in rows:
        topic = normalize_topic(row["topic"], row["topic"] or "当前知识点")
        attempts = int(row["attempts"] or 0)
        weight = max(attempts, 1)
        bucket = merged.setdefault(topic, {
            "topic": topic,
            "weighted_mastery": 0.0,
            "weighted_score": 0.0,
            "weighted_p_init": 0.0,
            "weighted_p_transit": 0.0,
            "weighted_p_guess": 0.0,
            "weighted_p_slip": 0.0,
            "weighted_decay_rate": 0.0,
            "weight": 0,
            "attempts": 0,
            "correct_count": 0,
            "wrong_count": 0,
            "review_count": 0,
            "last_reviewed_at": None,
            "last_action": None,
            "updated_at": None,
        })
        p_mastery = float(row["p_mastery"] or row["score"] or 0.3)
        bucket["weighted_mastery"] += p_mastery * weight
        bucket["weighted_score"] += float(row["score"] or p_mastery) * weight
        bucket["weighted_p_init"] += float(row["p_init"] or 0.3) * weight
        bucket["weighted_p_transit"] += float(row["p_transit"] or 0.1) * weight
        bucket["weighted_p_guess"] += float(row["p_guess"] or 0.25) * weight
        bucket["weighted_p_slip"] += float(row["p_slip"] or 0.1) * weight
        bucket["weighted_decay_rate"] += float(row["decay_rate"] or 0.05) * weight
        bucket["weight"] += weight
        bucket["attempts"] += attempts
        bucket["correct_count"] += int(row["correct_count"] or 0)
        bucket["wrong_count"] += int(row["wrong_count"] or 0)
        bucket["review_count"] = max(bucket["review_count"], int(row["review_count"] or 0))
        if row["last_reviewed_at"] and (
            not bucket["last_reviewed_at"] or str(row["last_reviewed_at"]) > str(bucket["last_reviewed_at"])
        ):
            bucket["last_reviewed_at"] = row["last_reviewed_at"]
        if row["updated_at"] and (
            not bucket["updated_at"] or str(row["updated_at"]) > str(bucket["updated_at"])
        ):
            bucket["updated_at"] = row["updated_at"]
        if (
            not bucket["last_action"]
            or _action_priority(row["last_action"]) < _action_priority(bucket["last_action"])
        ):
            bucket["last_action"] = row["last_action"]

    result = []
    for item in merged.values():
        weight = max(item.pop("weight"), 1)
        item["p_mastery"] = item.pop("weighted_mastery") / weight
        item["score"] = item.pop("weighted_score") / weight
        item["p_init"] = item.pop("weighted_p_init") / weight
        item["p_transit"] = item.pop("weighted_p_transit") / weight
        item["p_guess"] = item.pop("weighted_p_guess") / weight
        item["p_slip"] = item.pop("weighted_p_slip") / weight
        item["decay_rate"] = item.pop("weighted_decay_rate") / weight
        result.append(item)
    result.sort(key=lambda item: (item["p_mastery"], -item["attempts"], item["topic"]))
    return result


class ResourceReference(BaseModel):
    resource_id: int = Field(gt=0)
    version: int = Field(gt=0)
    current_version: int | None = Field(default=None, gt=0)
    selection: ArtifactSelection | None = None
    selection_source: Literal["preview"] | None = None


class ChatRequest(BaseModel):
    message: str
    student_id: str = "default"
    user_id: str = "local_user"
    mode: str = "sequential"
    attachment_ids: list[str] = Field(default_factory=list)
    required_intents: list[str] = Field(default_factory=list)
    resource_ref: ResourceReference | None = None


class ChatModeRequest(BaseModel):
    message: str
    student_id: str = "default"
    user_id: str = "local_user"
    history: list = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    resource_ref: ResourceReference | None = None
    pdf_ref: dict | None = None


class QuizSubmitRequest(BaseModel):
    student_id: str
    quiz_id: int
    user_answer: str


class TutorFollowupRequest(BaseModel):
    student_id: str
    user_id: str = "local_user"
    quiz_id: int


class ActivityRequest(BaseModel):
    student_id: str | None = None
    seconds: int = 30


class LearningEventRequest(BaseModel):
    student_id: str | None = None
    event_type: str
    resource_id: int | None = None
    resource_type: str | None = None
    title: str | None = None
    topic: str | None = None
    value: int | None = 0
    metadata: dict = Field(default_factory=dict)


class NoteRequest(BaseModel):
    id: str | None = None
    student_id: str | None = None
    type: str = "text"
    content: str
    source: str | None = None
    options: list = Field(default_factory=list)
    answer: str | None = None
    explanation: str | None = None
    html: str | None = None
    created_at: str | None = None


class NotesSyncRequest(BaseModel):
    student_id: str | None = None
    notes: list[NoteRequest] = Field(default_factory=list)


def _normalize_text(value) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s,，。.!！?？;；:：、()（）\[\]{}<>《》\"'`_-]+", "", normalized)


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value or "").strip() else []


def _normalize_judge_answer(value) -> bool | None:
    raw = str(value or "").strip().lower()
    if raw == "a":
        return True
    if raw == "b":
        return False
    text = re.sub(r"^[a-z](?:[.、)、\s]+)", "", raw)
    if text in {"true", "t", "yes", "y", "1", "correct", "right", "对", "正确", "是", "真"}:
        return True
    if text in {"false", "f", "no", "n", "0", "incorrect", "wrong", "错", "错误", "否", "假"}:
        return False
    return None


def _is_quiz_answer_correct(question: dict, user_answer: str, answer_data: dict) -> bool:
    correct_answer = answer_data.get("answer", "")
    normalized_user = str(user_answer or "").strip().lower()
    normalized_correct = str(correct_answer or "").strip().lower()
    if not normalized_user or not normalized_correct:
        return False

    answer_type = str(question.get("type") or "").lower().replace("_", "-")
    if answer_type in {"judge", "true-false", "truefalse", "boolean", "tf", "judgement", "judgment", "判断题", "判断"}:
        expected = _normalize_judge_answer(correct_answer)
        candidates = [*(_as_list(answer_data.get("accepted_answers"))), *(_as_list(question.get("accepted_answers")))]
        if expected is None:
            for candidate in candidates:
                expected = _normalize_judge_answer(candidate)
                if expected is not None:
                    break
        actual = _normalize_judge_answer(user_answer)
        return expected is not None and actual is not None and expected == actual
    if normalized_user == normalized_correct:
        return True

    user_letter = normalized_user[:1] if re.match(r"^[a-z][.、)、\s]", normalized_user) else ""
    correct_letter = normalized_correct[:1] if re.fullmatch(r"[a-z]", normalized_correct) else ""
    if user_letter and correct_letter and user_letter == correct_letter:
        return True

    strip_prefix = lambda value: re.sub(r"^[a-z][.、)、\s]*", "", value).strip()
    user_text = strip_prefix(normalized_user)
    correct_text = strip_prefix(normalized_correct)
    if bool(
        user_text
        and correct_text
        and (
            user_text == correct_text
            or user_text.endswith(correct_text)
            or correct_text.endswith(user_text)
        )
    ):
        return True

    aliases = _as_list(answer_data.get("accepted_answers")) + _as_list(question.get("accepted_answers"))
    candidates = list(dict.fromkeys([str(correct_answer), *aliases]))
    compact_user = _normalize_text(user_answer)
    compact_candidates = [_normalize_text(candidate) for candidate in candidates if _normalize_text(candidate)]
    if compact_user and any(
        compact_user == candidate
        or (len(candidate) >= 2 and candidate in compact_user)
        or (len(compact_user) >= 2 and compact_user in candidate)
        for candidate in compact_candidates
    ):
        return True

    keywords = _as_list(answer_data.get("answer_keywords")) + _as_list(question.get("answer_keywords"))
    compact_keywords = [_normalize_text(keyword) for keyword in keywords if _normalize_text(keyword)]
    if answer_type in {"short", "short-answer", "简答题", "code", "calculation"} and compact_keywords:
        matched = sum(1 for keyword in compact_keywords if keyword in compact_user)
        return matched >= max(1, round(len(compact_keywords) * 0.6))
    return False


# ── Chat ──────────────────────────────────────────────

@router.post("/chat/attachments")
async def upload_chat_attachments(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > 8:
        raise HTTPException(400, "A maximum of 8 attachments is allowed per upload")

    attachments = []
    for upload in files:
        try:
            content = await upload.read()
            attachment = await asyncio.to_thread(
                create_attachment,
                upload.filename or "attachment",
                content,
            )
            attachments.append(attachment)
        except Exception as exc:
            attachments.append({
                "id": f"error-{uuid.uuid4().hex}",
                "name": upload.filename or "attachment",
                "status": "error",
                "characters": 0,
                "preview": "",
                "error": str(exc),
            })
    return {"attachments": attachments}


@router.delete("/chat/attachments/{attachment_id}")
def remove_chat_attachment(attachment_id: str):
    if not delete_attachment(attachment_id):
        raise HTTPException(404, "Attachment not found")
    return {"deleted": True, "id": attachment_id}


@router.post("/chat")
async def chat(req: ChatRequest):
    with usage_scope(req.user_id, req.student_id):
        result = process_message(
            req.student_id,
            req.message,
            mode=req.mode,
            user_id=req.user_id,
            attachment_ids=req.attachment_ids,
            required_intents=req.required_intents,
            resource_ref=req.resource_ref.model_dump() if req.resource_ref else None,
        )
    return result


@router.post("/chat/talk")
async def chat_talk(req: ChatModeRequest):
    """对话模式：直接用LLM回复"""
    with usage_scope(req.user_id, req.student_id):
        result = process_chat_message(
            req.student_id,
            req.message,
            req.history,
            user_id=req.user_id,
            attachment_ids=req.attachment_ids,
            resource_ref=req.resource_ref.model_dump() if req.resource_ref else None,
            pdf_ref=req.pdf_ref,
        )
    return result


@router.post("/chat/talk/stream")
async def chat_talk_stream(req: ChatModeRequest):
    q = queue.Queue()

    def producer():
        try:
            with usage_scope(req.user_id, req.student_id):
                for event in process_chat_message_stream(
                    req.student_id,
                    req.message,
                    req.history,
                    user_id=req.user_id,
                    attachment_ids=req.attachment_ids,
                    resource_ref=req.resource_ref.model_dump() if req.resource_ref else None,
                    pdf_ref=req.pdf_ref,
                ):
                    q.put(event)
        except Exception as e:
            q.put({"event": "error", "data": json.dumps({"message": str(e)}, ensure_ascii=False)})
        finally:
            q.put(None)

    threading.Thread(target=producer, daemon=True).start()

    async def event_generator():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is None:
                break
            yield item

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
        ping=10,
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    q = queue.Queue()

    def producer():
        try:
            with usage_scope(req.user_id, req.student_id):
                for event in process_message_stream(
                    req.student_id,
                    req.message,
                    mode=req.mode,
                    user_id=req.user_id,
                    attachment_ids=req.attachment_ids,
                    required_intents=req.required_intents,
                    resource_ref=req.resource_ref.model_dump() if req.resource_ref else None,
                ):
                    q.put(event)
        except Exception as e:
            q.put({"event": "error", "data": json.dumps({"message": str(e)}, ensure_ascii=False)})
        finally:
            q.put(None)

    thread = threading.Thread(target=producer, daemon=True)
    thread.start()

    async def event_generator():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is None:
                break
            yield item

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
        ping=10,
    )


# ── Profile ───────────────────────────────────────────

@router.get("/profile/{student_id}")
def get_profile(student_id: str):
    db = get_db()
    row = db.execute(
        "SELECT profile_json, version, created_at FROM profiles WHERE student_id = ? ORDER BY version DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Profile not found")
    return {
        "student_id": student_id,
        "profile": json.loads(row["profile_json"]),
        "version": row["version"],
        "created_at": row["created_at"],
        "evidence": build_profile_evidence(student_id),
    }


@router.get("/profile/{student_id}/evidence")
def get_profile_evidence(student_id: str):
    return build_profile_evidence(student_id)


# ── Resources ─────────────────────────────────────────

@router.get("/resources/{student_id}")
def get_resources(student_id: str):
    db = get_db()
    rows = db.execute(
        "SELECT id FROM resources WHERE student_id = ? AND EXISTS("
        "SELECT 1 FROM resource_versions v WHERE v.resource_id=resources.id AND v.version=resources.current_version"
        ") ORDER BY created_at DESC,id DESC",
        (student_id,),
    ).fetchall()
    return [read_artifact(r["id"], student_id=student_id) for r in rows]


@router.get("/resource/{resource_id}")
def get_resource(resource_id: int):
    db = get_db()
    r = db.execute(
        "SELECT id, student_id, type, title, content, metadata_json, created_at FROM resources WHERE id = ?",
        (resource_id,),
    ).fetchone()
    if not r:
        raise HTTPException(404, "Resource not found")
    return read_artifact(resource_id)


# Quiz routes

@router.post("/quiz/submit")
def submit_quiz(req: QuizSubmitRequest):
    db = get_db()
    quiz = db.execute(
        "SELECT id, question_json, answer_json FROM quizzes WHERE id = ? AND student_id = ?",
        (req.quiz_id, req.student_id),
    ).fetchone()
    if not quiz:
        raise HTTPException(404, "Quiz not found")

    answer_data = json.loads(quiz["answer_json"])
    correct_answer = answer_data.get("answer", "")
    question = json.loads(quiz["question_json"])
    is_correct = 1 if _is_quiz_answer_correct(question, req.user_answer, answer_data) else 0

    db.execute(
        "INSERT INTO quiz_records (student_id, quiz_id, user_answer, is_correct) VALUES (?, ?, ?, ?)",
        (req.student_id, req.quiz_id, req.user_answer, is_correct),
    )
    db.commit()
    mastery = update_mastery_from_quiz(
        req.student_id,
        question,
        bool(is_correct),
    )

    return {
        "quiz_id": req.quiz_id,
        "user_answer": req.user_answer,
        "correct_answer": correct_answer,
        "is_correct": bool(is_correct),
        "explanation": answer_data.get("explanation", ""),
        "mastery": mastery,
        "tutor_action": {
            "type": mastery["action"],
            "message": mastery["message"],
            "topic": mastery["topic"],
            "executable": mastery["action"] != "advance",
            "label": {
                "retry": "生成同类变式题",
                "remediate": "生成补讲与降阶题",
                "reinforce": "生成迁移巩固题",
            }.get(mastery["action"]),
        },
    }


@router.post("/agent/followup")
def run_agent_followup(req: TutorFollowupRequest):
    db = get_db()
    quiz = db.execute(
        "SELECT question_json FROM quizzes WHERE id = ? AND student_id = ?",
        (req.quiz_id, req.student_id),
    ).fetchone()
    if not quiz:
        raise HTTPException(404, "Quiz not found")

    question = json.loads(quiz["question_json"])
    topic = normalize_topic(
        question.get("topic")
        or question.get("knowledge_point")
        or question.get("related_topic")
        or question.get("question")
        or "当前知识点",
        "当前知识点",
    )[:42]
    mastery = db.execute(
        "SELECT topic, score, attempts, correct_count, wrong_count, last_action "
        "FROM mastery_states WHERE student_id = ? AND topic = ?",
        (req.student_id, topic),
    ).fetchone()
    if not mastery:
        raise HTTPException(409, "Please submit the quiz before requesting a follow-up")

    state = {**dict(mastery), "action": mastery["last_action"]}
    with usage_scope(req.user_id, req.student_id):
        return execute_tutor_followup(req.student_id, req.quiz_id, state, user_id=req.user_id)


@router.get("/agent/state/{student_id}")
def get_agent_state(student_id: str):
    db = get_db()
    run = db.execute(
        "SELECT id, request, plan_json, reflection_json, created_at "
        "FROM tutor_runs WHERE student_id = ? ORDER BY id DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    return {
        "student_id": student_id,
        "mastery": get_mastery_state(student_id),
        "latest_run": {
            "id": run["id"],
            "request": run["request"],
            "plan": json.loads(run["plan_json"]),
            "reflections": json.loads(run["reflection_json"] or "[]"),
            "created_at": run["created_at"],
        } if run else None,
    }


@router.get("/bkt/{student_id}")
def get_bkt_data(student_id: str):
    """返回每个知识点的完整 BKT 数据和遗忘曲线预测。
    student_id 可以是单个会话ID，也可以是 user_id（聚合所有会话）。"""
    db = get_db()

    # 先尝试作为 student_id 查找
    session_ids = [row["id"] for row in db.execute(
        "SELECT id FROM students WHERE id = ?", (student_id,)
    ).fetchall()]

    # 如果没有匹配，当作 user_id 聚合所有本地会话
    if not session_ids:
        session_ids = get_owned_session_ids(student_id)

    if not session_ids:
        return {"student_id": student_id, "topics": []}

    placeholders = ", ".join("?" for _ in session_ids)
    rows = db.execute(
        f"SELECT topic, p_mastery, p_init, p_transit, p_guess, p_slip, decay_rate, "
        f"score, attempts, correct_count, wrong_count, review_count, last_reviewed_at, "
        f"last_action, updated_at "
        f"FROM mastery_states WHERE student_id IN ({placeholders}) ORDER BY p_mastery ASC",
        session_ids,
    ).fetchall()

    topics = []
    for row in _merge_mastery_rows(rows):
        p_mastery = float(row["p_mastery"] or row["score"] or 0.3)
        params = BKTParams(
            p_init=float(row["p_init"] or 0.3),
            p_transit=float(row["p_transit"] or 0.1),
            p_guess=float(row["p_guess"] or 0.25),
            p_slip=float(row["p_slip"] or 0.1),
            decay_rate=float(row["decay_rate"] or 0.05),
        )
        review_count = int(row["review_count"] or 0)

        # 生成遗忘曲线预测数据（0~72小时）
        curve_hours = [0, 1, 2, 4, 8, 12, 24, 36, 48, 72]
        forgetting_curve = []
        for h in curve_hours:
            p_decayed = apply_forgetting(p_mastery, h, params, review_count)
            forgetting_curve.append({"hours": h, "p_mastery": round(p_decayed, 4)})

        # 不同复习次数下的遗忘曲线
        review_curves = {}
        for rc in [0, 1, 3, 5, 10]:
            curve = []
            for h in curve_hours:
                p_decayed = apply_forgetting(p_mastery, h, params, rc)
                curve.append({"hours": h, "p_mastery": round(p_decayed, 4)})
            review_curves[str(rc)] = curve

        attempts = int(row["attempts"] or 0)
        correct = int(row["correct_count"] or 0)

        topics.append({
            "topic": row["topic"],
            "p_mastery": round(p_mastery, 4),
            "params": {
                "p_init": params.p_init,
                "p_transit": params.p_transit,
                "p_guess": params.p_guess,
                "p_slip": params.p_slip,
                "decay_rate": params.decay_rate,
            },
            "attempts": attempts,
            "correct_count": correct,
            "wrong_count": int(row["wrong_count"] or 0),
            "accuracy": round(correct / attempts * 100, 1) if attempts else 0,
            "review_count": review_count,
            "last_reviewed_at": row["last_reviewed_at"],
            "last_action": row["last_action"],
            "updated_at": row["updated_at"],
            "forgetting_curve": forgetting_curve,
            "review_curves": review_curves,
        })

    return {"student_id": student_id, "topics": topics}


@router.get("/review/{student_id}")
def get_review_tasks(student_id: str):
    """计算间隔重复调度，返回今日待复习和即将到期的知识点。
    student_id 可以是单个会话ID，也可以是 user_id（聚合所有会话）。"""
    db = get_db()

    session_ids = [row["id"] for row in db.execute(
        "SELECT id FROM students WHERE id = ?", (student_id,)
    ).fetchall()]
    if not session_ids:
        session_ids = get_owned_session_ids(student_id)
    if not session_ids:
        return {"student_id": student_id, "due_topics": [], "upcoming_topics": [], "total_due": 0}

    placeholders = ", ".join("?" for _ in session_ids)
    rows = db.execute(
        f"SELECT topic, p_mastery, p_init, p_transit, p_guess, p_slip, decay_rate, "
        f"score, attempts, correct_count, wrong_count, review_count, last_reviewed_at, "
        f"last_action, updated_at "
        f"FROM mastery_states WHERE student_id IN ({placeholders}) AND attempts > 0",
        session_ids,
    ).fetchall()

    topics = []
    for row in _merge_mastery_rows(rows):
        attempts = int(row["attempts"] or 0)
        correct = int(row["correct_count"] or 0)
        topics.append({
            "topic": row["topic"],
            "p_mastery": float(row["p_mastery"] or row["score"] or 0.3),
            "p_init": float(row["p_init"] or 0.3),
            "p_transit": float(row["p_transit"] or 0.1),
            "p_guess": float(row["p_guess"] or 0.25),
            "p_slip": float(row["p_slip"] or 0.1),
            "decay_rate": float(row["decay_rate"] or 0.05),
            "review_count": int(row["review_count"] or 0),
            "last_reviewed_at": row["last_reviewed_at"],
            "attempts": attempts,
            "accuracy": round(correct / attempts * 100, 1) if attempts else 0,
        })

    result = compute_review_schedule(topics)
    result["student_id"] = student_id
    return result


@router.get("/quiz/{student_id}")
def get_quizzes(student_id: str):
    db = get_db()
    rows = db.execute(
        "SELECT q.id, q.question_json, q.answer_json, q.resource_id, r.title as resource_title "
        "FROM quizzes q LEFT JOIN resources r ON q.resource_id = r.id "
        "WHERE q.student_id = ? ORDER BY q.created_at DESC",
        (student_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "question": json.loads(r["question_json"]),
            "resource_id": r["resource_id"],
            "resource_title": r["resource_title"],
        }
        for r in rows
    ]


@router.get("/quiz-records/{student_id}")
def get_quiz_records(student_id: str):
    db = get_db()
    rows = db.execute(
        "SELECT qr.id, qr.quiz_id, qr.user_answer, qr.is_correct, qr.timestamp, "
        "q.question_json FROM quiz_records qr JOIN quizzes q ON qr.quiz_id = q.id "
        "WHERE qr.student_id = ? ORDER BY qr.timestamp DESC",
        (student_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "quiz_id": r["quiz_id"],
            "user_answer": r["user_answer"],
            "is_correct": bool(r["is_correct"]),
            "timestamp": r["timestamp"],
            "question": json.loads(r["question_json"]),
        }
        for r in rows
    ]


# ── Learning Path ─────────────────────────────────────

@router.get("/learning-path/{student_id}")
def get_learning_path(student_id: str):
    db = get_db()
    row = db.execute(
        "SELECT path_json, status, created_at FROM learning_paths WHERE student_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Learning path not found")
    return {
        "student_id": student_id,
        "path": json.loads(row["path_json"]),
        "status": row["status"],
        "created_at": row["created_at"],
    }


# ── Assessment ────────────────────────────────────────

@router.get("/assessment/{student_id}")
def get_assessment(student_id: str):
    db = get_db()
    profile_row = db.execute(
        "SELECT profile_json FROM profiles WHERE student_id = ? ORDER BY version DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    if not profile_row:
        raise HTTPException(404, "Profile not found")

    records_rows = db.execute(
        "SELECT qr.user_answer, qr.is_correct, q.question_json "
        "FROM quiz_records qr JOIN quizzes q ON qr.quiz_id = q.id "
        "WHERE qr.student_id = ? ORDER BY qr.timestamp DESC LIMIT 10",
        (student_id,),
    ).fetchall()

    quiz_records = [
        {
            "question": json.loads(r["question_json"]).get("question", ""),
            "user_answer": r["user_answer"],
            "is_correct": bool(r["is_correct"]),
        }
        for r in records_rows
    ]

    profile = json.loads(profile_row["profile_json"])
    assessment = generate_assessment_report(profile, quiz_records, "生成学习评估报告")

    return assessment


# ── Conversations ─────────────────────────────────────

@router.get("/conversations/{student_id}")
def get_conversations(student_id: str, user_id: str = "local_user"):
    db = get_db()
    owner = db.execute("SELECT owner_id FROM students WHERE id=?", (student_id,)).fetchone()
    if owner is None:
        return []
    if owner["owner_id"] != user_id:
        raise HTTPException(403, "当前用户不能访问此会话")
    rows = db.execute(
        "SELECT c.id,c.role,c.content,c.timestamp,c.artifact_action_json,"
        "rr.resource_id,rr.version,rr.selection_json,rv.title,uc.id AS understanding_check_id FROM conversations c "
        "LEFT JOIN conversation_resource_refs rr ON rr.conversation_id=c.id "
        "LEFT JOIN resource_versions rv ON rv.resource_id=rr.resource_id AND rv.version=rr.version "
        "LEFT JOIN understanding_checks uc ON uc.conversation_id=c.id "
        "WHERE c.student_id=? ORDER BY c.id ASC",
        (student_id,),
    ).fetchall()
    from services.pdf_chat import get_pdf_chat_metadata
    pdf_metadata = get_pdf_chat_metadata([r["id"] for r in rows], student_id, user_id)
    return [
        {"id": r["id"], "role": r["role"], "content": r["content"], "timestamp": r["timestamp"],
         **pdf_metadata.get(r["id"], {}),
         "artifact_action": json.loads(r["artifact_action_json"]) if r["artifact_action_json"] else None,
         "understanding_check_id": r["understanding_check_id"],
         "resource_reference": ({"resource_id": r["resource_id"], "version": r["version"],
                                 "title": r["title"],
                                 **({"selection": json.loads(r["selection_json"])} if r["selection_json"] else {})}
                                if r["resource_id"] is not None else None)}
        for r in rows
    ]


# ── Sessions ──────────────────────────────────────────

@router.get("/sessions")
def list_sessions(user_id: str | None = Query(default=None)):
    """列出所有会话（去重student_id，取最新消息）"""
    db = get_db()
    session_ids = get_owned_session_ids(user_id or "local_user")
    if not session_ids:
        return []
    placeholders = ", ".join("?" for _ in session_ids)
    rows = db.execute(
        "SELECT student_id, content, timestamp FROM conversations "
        f"WHERE role = 'user' AND student_id IN ({placeholders}) GROUP BY student_id "
        "ORDER BY MAX(timestamp) DESC"
        ,
        session_ids,
    ).fetchall()

    sessions = []
    for r in rows:
        sid = r["student_id"]
        # 获取该会话的消息数
        count = db.execute(
            "SELECT COUNT(*) as cnt FROM conversations WHERE student_id = ?", (sid,)
        ).fetchone()["cnt"]
        # 获取最新画像
        profile_row = db.execute(
            "SELECT profile_json FROM profiles WHERE student_id = ? ORDER BY version DESC LIMIT 1", (sid,)
        ).fetchone()
        profile = json.loads(profile_row["profile_json"]) if profile_row else None

        sessions.append({
            "student_id": sid,
            "last_message": r["content"][:50],
            "message_count": count,
            "updated_at": r["timestamp"],
            "course": profile.get("当前课程", "") if profile else "",
        })

    # Uploaded originals remain discoverable before the first question is sent.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pdf_documents'").fetchone():
        pdf_only = db.execute(
            "SELECT d.student_id,d.title,d.created_at FROM pdf_documents d "
            "WHERE d.user_id=? AND NOT EXISTS (SELECT 1 FROM conversations c "
            "WHERE c.student_id=d.student_id AND c.role='user') "
            "AND d.id=(SELECT p.id FROM pdf_documents p WHERE p.student_id=d.student_id "
            "ORDER BY p.created_at DESC,p.id DESC LIMIT 1)", (user_id or "local_user",)
        ).fetchall()
        sessions.extend({"student_id": r["student_id"], "last_message": f"文档 · {r['title']}",
                         "message_count": 0, "updated_at": r["created_at"], "course": ""} for r in pdf_only)
        sessions.sort(key=lambda item: item["updated_at"] or "", reverse=True)
    return sessions


@router.post("/users/{user_id}/activity")
def add_learning_activity(user_id: str, req: ActivityRequest):
    return record_learning_activity(user_id, req.student_id, req.seconds)


@router.post("/users/{user_id}/events")
def add_learning_event(user_id: str, req: LearningEventRequest):
    return record_learning_event(
        user_id=user_id,
        student_id=req.student_id,
        event_type=req.event_type,
        resource_id=req.resource_id,
        resource_type=req.resource_type,
        title=req.title,
        topic=req.topic,
        value=req.value,
        metadata=req.metadata,
    )


def _note_row_to_dict(row) -> dict:
    try:
        options = json.loads(row["options_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        options = []
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "studentId": row["student_id"],
        "type": row["type"],
        "content": row["content"],
        "source": row["source"],
        "options": options,
        "answer": row["answer"],
        "explanation": row["explanation"],
        "html": row["html"],
        "createdAt": row["created_at"],
    }


def _upsert_note(user_id: str, req: NoteRequest) -> dict:
    db = get_db()
    note_id = req.id or f"note_{int(time.time() * 1000000)}"
    db.execute(
        "INSERT INTO notes "
        "(id, user_id, student_id, type, content, source, options_json, answer, explanation, html, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP) "
        "ON CONFLICT(id) DO UPDATE SET "
        "user_id=excluded.user_id, student_id=excluded.student_id, type=excluded.type, "
        "content=excluded.content, source=excluded.source, options_json=excluded.options_json, "
        "answer=excluded.answer, explanation=excluded.explanation, html=excluded.html, updated_at=CURRENT_TIMESTAMP",
        (
            note_id,
            user_id,
            req.student_id,
            req.type,
            req.content,
            req.source,
            json.dumps(req.options or [], ensure_ascii=False),
            req.answer,
            req.explanation,
            req.html,
            req.created_at,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    return _note_row_to_dict(row)


@router.get("/users/{user_id}/notes")
def get_user_notes(user_id: str):
    user_ids = get_local_user_ids(user_id)
    placeholders = ", ".join("?" for _ in user_ids)
    rows = get_db().execute(
        f"SELECT * FROM notes WHERE user_id IN ({placeholders}) ORDER BY created_at DESC, updated_at DESC",
        user_ids,
    ).fetchall()
    return [_note_row_to_dict(row) for row in rows]


@router.post("/users/{user_id}/notes")
def add_user_note(user_id: str, req: NoteRequest):
    note = _upsert_note(user_id, req)
    record_learning_event(
        user_id=user_id,
        student_id=req.student_id,
        event_type="note_added",
        resource_type=req.type,
        title=req.source or req.type,
        topic=(req.content or "")[:60],
        metadata={"source": "notes_api", "content": (req.content or "")[:240]},
    )
    return note


@router.post("/users/{user_id}/notes/sync")
def sync_user_notes(user_id: str, req: NotesSyncRequest):
    synced = []
    for note in req.notes:
        if not note.content:
            continue
        if not note.student_id:
            note.student_id = req.student_id
        synced.append(_upsert_note(user_id, note))
    return {"count": len(synced), "notes": synced}


@router.delete("/users/{user_id}/notes/{note_id}")
def delete_user_note(user_id: str, note_id: str):
    user_ids = get_local_user_ids(user_id)
    placeholders = ", ".join("?" for _ in user_ids)
    get_db().execute(
        f"DELETE FROM notes WHERE id = ? AND user_id IN ({placeholders})",
        [note_id, *user_ids],
    )
    get_db().commit()
    return {"deleted": note_id}


@router.get("/users/{user_id}/space")
def user_space(user_id: str):
    return get_user_space(user_id)


@router.delete("/sessions/{student_id}")
def delete_session(student_id: str, user_id: str | None = Query(default=None)):
    if user_id and student_id not in get_owned_session_ids(user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    """删除一个会话的所有数据"""
    db = get_db()
    from services.pdf_documents import detach_session_documents
    pdf_paths = detach_session_documents(student_id)
    db.execute("DELETE FROM tutor_followups WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM understanding_checks WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM learning_events WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM notes WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM quiz_records WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM quizzes WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM resources WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM conversations WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM profiles WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM learning_paths WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM mastery_states WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM tutor_runs WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM students WHERE id = ?", (student_id,))
    db.commit()
    for path in pdf_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Metadata is gone, so an unavailable original cannot remain exposed.
            import logging
            logging.getLogger(__name__).warning("Could not remove orphan PDF file %s", path.name)
    return {"deleted": student_id}
