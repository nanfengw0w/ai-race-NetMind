import json

from db.database import get_db


def _clamp_score(value: float) -> int:
    return max(0, min(100, round(value)))


def _resource_topics(rows) -> set[str]:
    topics: set[str] = set()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        candidates = metadata.get("related_topics") or []
        if isinstance(candidates, str):
            candidates = [candidates]
        if not candidates and metadata.get("topic"):
            candidates = [metadata["topic"]]
        if not candidates and row["title"]:
            title = str(row["title"]).strip()
            candidates = [title.split(" - ", 1)[-1]]
        for topic in candidates:
            normalized = str(topic or "").strip()
            if normalized:
                topics.add(normalized)
    return topics


def build_profile_evidence(student_id: str) -> dict:
    db = get_db()
    resource_rows = db.execute(
        "SELECT id, type, title, metadata_json FROM resources WHERE student_id = ?",
        (student_id,),
    ).fetchall()
    quiz_summary = db.execute(
        "SELECT COUNT(*) AS attempts, COALESCE(SUM(is_correct), 0) AS correct "
        "FROM quiz_records WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    mastery_rows = db.execute(
        "SELECT topic, p_mastery, score, attempts FROM mastery_states "
        "WHERE student_id = ? AND attempts > 0",
        (student_id,),
    ).fetchall()
    activity = db.execute(
        "SELECT "
        "COUNT(DISTINCT CASE WHEN le.event_type = 'resource_view' AND r.id IS NOT NULL THEN r.id END) AS viewed_resources, "
        "COUNT(DISTINCT CASE WHEN le.event_type = 'resource_view' AND r.type = 'lab' THEN r.id END) AS viewed_labs, "
        "COALESCE(SUM(CASE WHEN le.event_type = 'resource_active' AND r.id IS NOT NULL THEN le.value ELSE 0 END), 0) AS active_seconds, "
        "SUM(CASE WHEN le.event_type = 'note_added' THEN 1 ELSE 0 END) AS notes "
        "FROM learning_events le "
        "LEFT JOIN resources r ON r.id = le.resource_id AND r.student_id = le.student_id "
        "WHERE le.student_id = ?",
        (student_id,),
    ).fetchone()

    resource_count = len(resource_rows)
    attempts = int(quiz_summary["attempts"] or 0)
    correct = int(quiz_summary["correct"] or 0)
    viewed_resources = int(activity["viewed_resources"] or 0)
    viewed_labs = int(activity["viewed_labs"] or 0)
    active_seconds = int(activity["active_seconds"] or 0)
    note_count = int(activity["notes"] or 0)

    resource_topics = _resource_topics(resource_rows)
    mastery_topics = {str(row["topic"]).strip() for row in mastery_rows if row["topic"]}
    topic_count = len(resource_topics | mastery_topics)
    lab_count = sum(1 for row in resource_rows if row["type"] == "lab")

    if mastery_rows:
        weighted_total = 0.0
        total_weight = 0
        for row in mastery_rows:
            weight = max(1, int(row["attempts"] or 0))
            mastery = float(row["p_mastery"] or row["score"] or 0)
            weighted_total += mastery * weight
            total_weight += weight
        mastery_score = _clamp_score(weighted_total / max(total_weight, 1) * 100)
    else:
        mastery_score = None

    accuracy_score = _clamp_score(correct / attempts * 100) if attempts else None
    coverage_score = _clamp_score(topic_count / 8 * 100) if topic_count else None
    practice_score = (
        _clamp_score(viewed_labs * 35 + min(note_count, 3) * 10)
        if lab_count or viewed_labs or note_count
        else None
    )
    engagement_score = (
        _clamp_score(min(active_seconds / 1800, 1) * 70 + min(viewed_resources / 8, 1) * 30)
        if active_seconds or viewed_resources
        else None
    )

    metrics = [
        {
            "key": "mastery",
            "label": "掌握度",
            "value": mastery_score,
            "evidence": f"{len(mastery_rows)} 个知识点有答题追踪" if mastery_rows else "等待答题数据",
        },
        {
            "key": "accuracy",
            "label": "答题表现",
            "value": accuracy_score,
            "evidence": f"{correct}/{attempts} 题正确" if attempts else "尚未完成练习",
        },
        {
            "key": "coverage",
            "label": "知识覆盖",
            "value": coverage_score,
            "evidence": f"已涉及 {topic_count} 个知识点" if topic_count else "尚未形成知识足迹",
        },
        {
            "key": "practice",
            "label": "实践迁移",
            "value": practice_score,
            "evidence": (
                f"查看 {viewed_labs} 个实操，记录 {note_count} 条笔记"
                if lab_count or viewed_labs or note_count
                else "等待实操或笔记证据"
            ),
        },
        {
            "key": "engagement",
            "label": "学习投入",
            "value": engagement_score,
            "evidence": (
                f"活跃 {round(active_seconds / 60)} 分钟，查看 {viewed_resources} 个资源"
                if active_seconds or viewed_resources
                else "等待资源阅读记录"
            ),
        },
    ]

    measured_count = sum(metric["value"] is not None for metric in metrics)
    evidence_units = attempts + viewed_resources + topic_count + viewed_labs + note_count
    if measured_count >= 5 and evidence_units >= 12:
        confidence = "high"
    elif measured_count >= 3 and evidence_units >= 4:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "ready": bool(resource_count or attempts or mastery_rows or note_count),
        "confidence": confidence,
        "measured_count": measured_count,
        "metrics": metrics,
        "summary": {
            "resources": resource_count,
            "quiz_attempts": attempts,
            "topics": topic_count,
            "active_seconds": active_seconds,
            "viewed_resources": viewed_resources,
        },
    }
