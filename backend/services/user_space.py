import json
from collections import Counter, defaultdict
from datetime import date, timedelta

from db.database import get_db
from agents.topic_utils import normalize_topic
from services.usage_tracker import ensure_user


LEVEL_SCORE = {"weak": 1, "medium": 2, "strong": 3}
LEVEL_NAME = {1: "基础较弱", 2: "基础中等", 3: "基础扎实"}


KNOWLEDGE_DEPENDENCIES = [
    ("网络基础", "IP协议", "承接"),
    ("IP协议", "IPv4", "细化"),
    ("IP协议", "IPv6", "演进"),
    ("IPv4", "IPv4/IPv6对比", "对比"),
    ("IPv6", "IPv4/IPv6对比", "对比"),
    ("MAC地址", "ARP协议", "前置"),
    ("IPv4", "ARP协议", "协作"),
    ("以太网", "ARP协议", "承载"),
    ("IP协议", "路由协议", "转发"),
    ("IP协议", "子网划分", "规划"),
    ("DNS", "HTTP", "支撑"),
    ("TCP三次握手", "HTTP", "承载"),
    ("TCP三次握手", "TCP拥塞控制", "进阶"),
    ("Wireshark", "ARP协议", "验证"),
    ("Wireshark", "TCP三次握手", "验证"),
    ("Wireshark", "DNS", "验证"),
]

TOPIC_KEYWORDS = {
    "网络基础": ("网络基础", "计算机网络"),
    "IP协议": ("IP协议", "IP地址"),
    "IPv4": ("IPv4",),
    "IPv6": ("IPv6",),
    "IPv4/IPv6对比": ("IPv4/IPv6对比",),
    "ARP协议": ("ARP协议",),
    "MAC地址": ("MAC地址",),
    "以太网": ("以太网", "链路层"),
    "路由协议": ("路由协议", "路由"),
    "子网划分": ("子网划分", "子网掩码"),
    "DNS": ("DNS",),
    "HTTP": ("HTTP", "HTTPS"),
    "TCP三次握手": ("TCP三次握手",),
    "TCP拥塞控制": ("TCP拥塞控制",),
    "Wireshark": ("Wireshark",),
}


def _summary_value(value) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value if str(item).strip())
    return str(value or "").strip()


def _topic_status(score: int, attempts: int = 0) -> str:
    if attempts <= 0:
        return "planned"
    if score >= 80:
        return "mastered"
    if score >= 55:
        return "building"
    return "weak"


def _resource_type_name(value: str) -> str:
    return {
        "interactive": "互动教学",
        "lecture": "课程讲解",
        "quiz": "练习题",
        "mindmap": "思维导图",
        "ppt": "PPT课件",
        "lab": "实操案例",
        "learning_path": "学习路径",
        "assessment": "评估报告",
    }.get(value or "", value or "学习资源")


def _safe_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if parsed is not None else fallback


def _build_decision_flow(
    *,
    focus: str,
    observations: list[str],
    diagnosis: list[str],
    strategy: list[str],
    next_actions: list[dict],
    latest_run: dict | None,
    totals: dict,
) -> tuple[list[dict], dict]:
    plan = latest_run.get("plan") if latest_run else None
    reflections = latest_run.get("reflections") if latest_run else []
    reflections = reflections if isinstance(reflections, list) else []
    actions = plan.get("actions", []) if isinstance(plan, dict) else []
    actions = actions if isinstance(actions, list) else []

    passed = sum(1 for item in reflections if item.get("passed"))
    repaired = sum(1 for item in reflections if item.get("decision") == "repair_once")
    failed = len(reflections) - passed
    reflection_details = []
    for item in reflections[:4]:
        tool = _resource_type_name(item.get("tool"))
        if item.get("passed"):
            reflection_details.append(f"{tool}：质量检查通过")
        else:
            issues = "；".join(str(issue) for issue in item.get("issues", []) if str(issue).strip())
            reflection_details.append(f"{tool}：发现问题，{issues or '已进入修复'}")

    if not reflection_details:
        reflection_details = ["等待下一次资源生成后记录质量反思。"]

    action_details = [
        f"{_resource_type_name(action.get('tool'))}：{action.get('reason') or action.get('instruction') or '服务本轮学习目标'}"
        for action in actions[:4]
        if isinstance(action, dict)
    ]
    if not action_details:
        action_details = [
            f"{action.get('label') or _resource_type_name(action.get('tool'))}：{action.get('reason') or '承接当前学习目标'}"
            for action in next_actions[:4]
        ]

    flow = [
        {
            "id": "observe",
            "eyebrow": "Observe",
            "title": "观察学习现场",
            "summary": f"聚合学习时长、资源使用、答题和最近对话，定位到「{focus}」。",
            "details": observations[:4],
            "metrics": [
                {"label": "资源", "value": totals.get("resources", 0)},
                {"label": "答题", "value": totals.get("quiz_attempts", 0)},
            ],
            "status": "done" if observations else "pending",
        },
        {
            "id": "diagnose",
            "eyebrow": "Diagnose",
            "title": "诊断掌握状态",
            "summary": "把 BKT 掌握度、正确率和优势/薄弱点转成教学判断。",
            "details": diagnosis[:4],
            "metrics": [
                {"label": "正确率", "value": f"{totals.get('accuracy', 0)}%"},
                {"label": "活跃天数", "value": totals.get("active_days", 0)},
            ],
            "status": "done" if diagnosis else "pending",
        },
        {
            "id": "plan",
            "eyebrow": "Plan",
            "title": "规划本轮策略",
            "summary": (plan or {}).get("goal") or f"围绕「{focus}」安排下一轮学习动作。",
            "details": [
                *(([(plan or {}).get("strategy")] if (plan or {}).get("strategy") else [])[:1]),
                *strategy[:3],
            ],
            "metrics": [
                {"label": "工具数", "value": len(actions) or len(next_actions)},
                {"label": "来源", "value": (plan or {}).get("source", "fallback")},
            ],
            "status": "done" if latest_run else "pending",
        },
        {
            "id": "act",
            "eyebrow": "Act",
            "title": "调用学习工具",
            "summary": "将教学策略分派给课程讲解、练习、思维导图、实操或课件生成工具。",
            "details": action_details[:4],
            "metrics": [
                {"label": "已执行", "value": len(reflections) or len(action_details)},
            ],
            "status": "done" if action_details else "pending",
        },
        {
            "id": "reflect",
            "eyebrow": "Reflect",
            "title": "质量反思与修复",
            "summary": f"已检查 {len(reflections)} 个资源，{passed} 个通过，{repaired} 个触发修复。",
            "details": reflection_details[:4],
            "metrics": [
                {"label": "通过", "value": passed},
                {"label": "修复", "value": repaired},
                {"label": "待关注", "value": max(failed, 0)},
            ],
            "status": "attention" if failed else ("done" if reflections else "pending"),
        },
        {
            "id": "update",
            "eyebrow": "Update",
            "title": "回写学习状态",
            "summary": "生成结果、答题反馈和学习行为会继续更新画像、BKT 和下一轮推荐。",
            "details": [
                *(f"下一步：{action.get('label') or _resource_type_name(action.get('tool'))}，{action.get('reason') or '承接当前薄弱点'}" for action in next_actions[:3]),
                "学生完成练习后会继续刷新知识追踪和复习队列。",
            ],
            "metrics": [
                {"label": "焦点", "value": focus},
            ],
            "status": "next",
        },
    ]
    summary = {
        "request": latest_run.get("request") if latest_run else "",
        "created_at": latest_run.get("created_at") if latest_run else None,
        "tool_count": len(actions) or len(next_actions),
        "reflection_count": len(reflections),
        "passed_count": passed,
        "repair_count": repaired,
    }
    return flow, summary


def _match_graph_topic(topic: str) -> str | None:
    normalized = normalize_topic(topic, topic)
    if normalized in TOPIC_KEYWORDS:
        return normalized
    for graph_topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return graph_topic
    return normalized if normalized else None


def _build_agent_loop(totals: dict, topics: list[dict], recent_sessions: list[dict], db, session_ids: list[str]) -> dict:
    weak = [item for item in topics if item["score"] < 55][:3]
    strong = sorted(topics, key=lambda item: (-item["score"], -item["attempts"]))[:3]
    focus = weak[0]["topic"] if weak else (topics[0]["topic"] if topics else "当前知识点")
    observations = []

    if totals.get("resources"):
        observations.append(f"已累计生成 {totals.get('resources', 0)} 个学习资源，说明系统已经有可复用的学习材料。")
    if totals.get("quiz_attempts"):
        observations.append(
            f"已完成 {totals.get('quiz_attempts', 0)} 次答题，整体正确率 {totals.get('accuracy', 0)}%。"
        )
    if totals.get("today_learning_seconds"):
        minutes = max(1, round(totals.get("today_learning_seconds", 0) / 60))
        observations.append(f"今天已有约 {minutes} 分钟有效学习行为，可继续接续当前节奏。")
    if recent_sessions:
        observations.append(f"最近活跃对话是「{recent_sessions[0].get('title', '未命名会话')}」。")
    if not observations:
        observations.append("还缺少足够学习行为，下一轮会先建立基础画像和诊断题。")

    diagnosis = []
    if weak:
        diagnosis.append("优先薄弱点：" + "、".join(f"{item['topic']}({item['score']}分)" for item in weak))
    if strong:
        diagnosis.append("可迁移优势：" + "、".join(f"{item['topic']}({item['score']}分)" for item in strong))
    if totals.get("quiz_attempts", 0) == 0 and totals.get("resources", 0) > 0:
        diagnosis.append("已经有学习资源，但还缺少练习反馈，无法稳定更新掌握度。")
    if not diagnosis:
        diagnosis.append("当前更需要通过首轮讲解与诊断题确认真实基础。")

    strategy = []
    if weak:
        strategy.extend([
            f"围绕「{focus}」生成短补讲，先补概念断点。",
            "随后生成同类变式题，用答题结果更新 BKT 掌握度。",
        ])
    elif totals.get("quiz_attempts", 0) == 0:
        strategy.extend([
            "先把已有课程讲解转成诊断题，建立第一批掌握度证据。",
            "根据错题再决定是否补讲或进入实操案例。",
        ])
    else:
        strategy.extend([
            "保持当前学习节奏，优先安排迁移题和实操验证。",
            "掌握稳定后推进到依赖图中的下一个知识点。",
        ])

    placeholders = _placeholders(session_ids) if session_ids else ""
    latest_run = None
    latest_plan = None
    if session_ids:
        row = db.execute(
            f"SELECT request, plan_json, reflection_json, created_at FROM tutor_runs "
            f"WHERE student_id IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            session_ids,
        ).fetchone()
        if row:
            latest_plan = _safe_json(row["plan_json"], None)
            latest_reflections = _safe_json(row["reflection_json"], [])
            latest_run = {
                "request": row["request"],
                "created_at": row["created_at"],
                "plan": latest_plan if isinstance(latest_plan, dict) else None,
                "reflections": latest_reflections if isinstance(latest_reflections, list) else [],
            }
            if latest_plan:
                strategy.insert(0, f"最近一次 Agent 策略：{latest_plan.get('strategy', '已完成自适应规划')}")

    next_actions = []
    if latest_plan and isinstance(latest_plan.get("actions"), list):
        for action in latest_plan["actions"][:4]:
            next_actions.append({
                "tool": action.get("tool"),
                "label": _resource_type_name(action.get("tool")),
                "reason": action.get("reason") or action.get("instruction") or "承接本轮学习目标",
            })
    if not next_actions:
        next_actions = [
            {"tool": "lecture", "label": "课程讲解", "reason": f"先围绕「{focus}」补齐理解缺口"},
            {"tool": "quiz", "label": "练习题", "reason": "用答题结果更新知识追踪"},
        ]
    decision_flow, reflection_summary = _build_decision_flow(
        focus=focus,
        observations=observations,
        diagnosis=diagnosis,
        strategy=strategy,
        next_actions=next_actions,
        latest_run=latest_run,
        totals=totals,
    )

    return {
        "focus": focus,
        "observations": observations[:4],
        "diagnosis": diagnosis[:4],
        "strategy": strategy[:4],
        "next_actions": next_actions[:4],
        "latest_run": latest_run,
        "decision_flow": decision_flow,
        "reflection_summary": reflection_summary,
    }


def _build_knowledge_graph(topics: list[dict]) -> dict:
    topic_by_graph_id: dict[str, dict] = {}
    for item in topics:
        graph_topic = _match_graph_topic(item.get("topic"))
        if not graph_topic:
            continue
        current = topic_by_graph_id.get(graph_topic)
        if not current or item.get("attempts", 0) > current.get("attempts", 0):
            topic_by_graph_id[graph_topic] = item

    selected = set(topic_by_graph_id)
    for source, target, _ in KNOWLEDGE_DEPENDENCIES:
        if source in selected or target in selected:
            selected.add(source)
            selected.add(target)

    if not selected:
        selected.update(["网络基础", "IP协议", "ARP协议", "DNS", "TCP三次握手"])

    layer_map = {
        "网络基础": 0,
        "MAC地址": 0,
        "以太网": 0,
        "Wireshark": 0,
        "IP协议": 1,
        "IPv4": 2,
        "IPv6": 2,
        "DNS": 2,
        "TCP三次握手": 2,
        "ARP协议": 3,
        "子网划分": 3,
        "路由协议": 3,
        "HTTP": 3,
        "TCP拥塞控制": 3,
        "IPv4/IPv6对比": 4,
    }
    nodes = []
    for topic in sorted(selected, key=lambda value: (layer_map.get(value, 5), value)):
        stat = topic_by_graph_id.get(topic, {})
        score = int(stat.get("score") or 0)
        attempts = int(stat.get("attempts") or 0)
        nodes.append({
            "id": topic,
            "label": topic,
            "score": score,
            "attempts": attempts,
            "status": _topic_status(score, attempts),
            "layer": layer_map.get(topic, 5),
        })

    node_ids = {node["id"] for node in nodes}
    edges = [
        {"source": source, "target": target, "relation": relation}
        for source, target, relation in KNOWLEDGE_DEPENDENCIES
        if source in node_ids and target in node_ids
    ]
    frontier = [
        node["id"] for node in nodes
        if node["status"] in {"weak", "planned"} and any(edge["target"] == node["id"] for edge in edges)
    ][:4]
    return {
        "nodes": nodes,
        "edges": edges,
        "frontier": frontier,
    }


def _owned_sessions(user_id: str) -> list[str]:
    user_ids = _local_user_ids(user_id)
    placeholders = _placeholders(user_ids)
    rows = get_db().execute(
        f"SELECT id FROM students WHERE owner_id IN ({placeholders}) OR owner_id IS NULL",
        user_ids,
    ).fetchall()
    return [row["id"] for row in rows]


def get_owned_session_ids(user_id: str) -> list[str]:
    return _owned_sessions(user_id)


def _local_user_ids(user_id: str) -> list[str]:
    db = get_db()
    requested = str(user_id or "local_user")
    if requested == "local_user" or requested.startswith("learner_"):
        ids = {requested, "local_user"}
        for table in ("learning_activity", "learning_events", "token_usage"):
            try:
                rows = db.execute(
                    f"SELECT DISTINCT user_id FROM {table} "
                    "WHERE user_id IS NOT NULL AND (user_id = 'local_user' OR user_id LIKE 'learner_%')"
                ).fetchall()
                ids.update(row["user_id"] for row in rows if row["user_id"])
            except Exception:
                continue
        rows = db.execute(
            "SELECT DISTINCT owner_id AS user_id FROM students "
            "WHERE owner_id IS NOT NULL AND (owner_id = 'local_user' OR owner_id LIKE 'learner_%')"
        ).fetchall()
        ids.update(row["user_id"] for row in rows if row["user_id"])
        return sorted(ids)
    return [requested]


def get_local_user_ids(user_id: str) -> list[str]:
    return _local_user_ids(user_id)


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _contribution_level(seconds: int) -> int:
    if seconds <= 0:
        return 0
    if seconds < 10 * 60:
        return 1
    if seconds < 30 * 60:
        return 2
    if seconds < 60 * 60:
        return 3
    return 4


def _daily_contributions(db, user_ids: list[str]) -> dict:
    today = date.today()
    start_date = today - timedelta(days=364)
    placeholders = _placeholders(user_ids)
    activity_rows = db.execute(
        "SELECT DATE(created_at, 'localtime') AS activity_date, "
        "COALESCE(SUM(active_seconds), 0) AS seconds "
        "FROM learning_activity "
        f"WHERE user_id IN ({placeholders}) AND DATE(created_at, 'localtime') >= ? "
        "GROUP BY DATE(created_at, 'localtime')",
        [*user_ids, start_date.isoformat()],
    ).fetchall()
    token_rows = db.execute(
        "SELECT DATE(created_at, 'localtime') AS activity_date, "
        "COALESCE(SUM(total_tokens), 0) AS tokens "
        "FROM token_usage "
        f"WHERE user_id IN ({placeholders}) AND DATE(created_at, 'localtime') >= ? "
        "GROUP BY DATE(created_at, 'localtime')",
        [*user_ids, start_date.isoformat()],
    ).fetchall()
    seconds_by_date = {row["activity_date"]: int(row["seconds"] or 0) for row in activity_rows}
    tokens_by_date = {row["activity_date"]: int(row["tokens"] or 0) for row in token_rows}
    days = []
    for offset in range(365):
        current = start_date + timedelta(days=offset)
        day = current.isoformat()
        seconds = seconds_by_date.get(day, 0)
        days.append({
            "date": day,
            "seconds": seconds,
            "tokens": tokens_by_date.get(day, 0),
            "level": _contribution_level(seconds),
        })

    active_days = sum(1 for item in days if item["seconds"] > 0)
    streak = 0
    current = today
    while seconds_by_date.get(current.isoformat(), 0) > 0:
        streak += 1
        current -= timedelta(days=1)
    return {
        "start_date": start_date.isoformat(),
        "end_date": today.isoformat(),
        "active_days": active_days,
        "current_streak": streak,
        "days": days,
    }


def record_learning_activity(user_id: str, student_id: str | None, seconds: int) -> dict:
    resolved_user = ensure_user(user_id)
    active_seconds = max(0, min(int(seconds or 0), 120))
    if active_seconds:
        get_db().execute(
            "INSERT INTO learning_activity (user_id, student_id, active_seconds) VALUES (?, ?, ?)",
            (resolved_user, student_id, active_seconds),
        )
        get_db().commit()
    return {"recorded_seconds": active_seconds}


def record_learning_event(
    user_id: str,
    student_id: str | None,
    event_type: str,
    resource_id: int | None = None,
    resource_type: str | None = None,
    title: str | None = None,
    topic: str | None = None,
    value: int | None = 0,
    metadata: dict | None = None,
) -> dict:
    resolved_user = ensure_user(user_id)
    clean_event = str(event_type or "interaction").strip()[:48]
    clean_resource_type = str(resource_type or "").strip()[:48]
    clean_title = str(title or "").strip()[:180]
    clean_topic = str(topic or "").strip()[:120]
    numeric_value = max(0, min(int(value or 0), 3600))
    get_db().execute(
        "INSERT INTO learning_events "
        "(user_id, student_id, event_type, resource_id, resource_type, title, topic, value, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            resolved_user,
            student_id,
            clean_event,
            resource_id,
            clean_resource_type,
            clean_title,
            clean_topic,
            numeric_value,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    get_db().commit()
    return {"recorded": True, "event_type": clean_event, "value": numeric_value}


def get_learning_signal_summary(student_id: str, user_id: str | None = None) -> dict:
    db = get_db()
    resolved_user = ensure_user(user_id) if user_id else None
    user_ids = _local_user_ids(resolved_user) if resolved_user else []

    activity_row = db.execute(
        "SELECT "
        "COALESCE(SUM(active_seconds), 0) AS total_seconds, "
        "COALESCE(SUM(CASE WHEN DATE(created_at, 'localtime') = DATE('now', 'localtime') THEN active_seconds ELSE 0 END), 0) AS today_seconds, "
        "COALESCE(SUM(CASE WHEN created_at >= DATETIME('now', '-7 days') THEN active_seconds ELSE 0 END), 0) AS week_seconds "
        "FROM learning_activity WHERE student_id = ?",
        (student_id,),
    ).fetchone()

    user_activity = {"total_seconds": 0, "today_seconds": 0, "week_seconds": 0}
    if user_ids:
        row = db.execute(
            "SELECT "
            "COALESCE(SUM(active_seconds), 0) AS total_seconds, "
            "COALESCE(SUM(CASE WHEN DATE(created_at, 'localtime') = DATE('now', 'localtime') THEN active_seconds ELSE 0 END), 0) AS today_seconds, "
            "COALESCE(SUM(CASE WHEN created_at >= DATETIME('now', '-7 days') THEN active_seconds ELSE 0 END), 0) AS week_seconds "
            f"FROM learning_activity WHERE user_id IN ({_placeholders(user_ids)})",
            user_ids,
        ).fetchone()
        user_activity = {
            "total_seconds": int(row["total_seconds"] or 0),
            "today_seconds": int(row["today_seconds"] or 0),
            "week_seconds": int(row["week_seconds"] or 0),
        }

    resource_rows = db.execute(
        "SELECT type, COUNT(*) AS count, MAX(created_at) AS latest_at "
        "FROM resources WHERE student_id = ? GROUP BY type",
        (student_id,),
    ).fetchall()
    generated_resources = {
        row["type"]: {"count": int(row["count"] or 0), "latest_at": row["latest_at"]}
        for row in resource_rows
    }

    engagement_rows = db.execute(
        "SELECT resource_type, COUNT(*) AS events, COALESCE(SUM(value), 0) AS seconds, MAX(created_at) AS latest_at "
        "FROM learning_events WHERE student_id = ? AND resource_type IS NOT NULL AND resource_type != '' "
        "GROUP BY resource_type",
        (student_id,),
    ).fetchall()
    resource_engagement = {
        row["resource_type"]: {
            "events": int(row["events"] or 0),
            "seconds": int(row["seconds"] or 0),
            "latest_at": row["latest_at"],
        }
        for row in engagement_rows
    }

    recent_rows = db.execute(
        "SELECT event_type, resource_type, title, topic, value, metadata_json, created_at "
        "FROM learning_events WHERE student_id = ? ORDER BY id DESC LIMIT 16",
        (student_id,),
    ).fetchall()
    recent_events = []
    note_previews = []
    for row in recent_rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        event = {
            "event_type": row["event_type"],
            "resource_type": row["resource_type"],
            "title": row["title"],
            "topic": row["topic"],
            "value": int(row["value"] or 0),
            "created_at": row["created_at"],
        }
        recent_events.append(event)
        if row["event_type"] == "note_added":
            preview = str(metadata.get("content") or metadata.get("note_preview") or "").strip()
            if preview:
                note_previews.append(preview[:120])

    mastery_rows = db.execute(
        "SELECT topic, p_mastery, score, attempts, wrong_count, updated_at "
        "FROM mastery_states WHERE student_id = ? ORDER BY p_mastery ASC, wrong_count DESC",
        (student_id,),
    ).fetchall()
    weak_topic_stats = defaultdict(lambda: {"weighted_mastery": 0.0, "weight": 0, "attempts": 0, "wrong_count": 0})
    for row in mastery_rows:
        topic = normalize_topic(row["topic"], row["topic"] or "当前知识点")
        weight = max(int(row["attempts"] or 0), 1)
        stat = weak_topic_stats[topic]
        stat["weighted_mastery"] += float(row["p_mastery"] or row["score"] or 0.3) * weight
        stat["weight"] += weight
        stat["attempts"] += int(row["attempts"] or 0)
        stat["wrong_count"] += int(row["wrong_count"] or 0)
    weak_topics = sorted([
        {
            "topic": topic,
            "p_mastery": round(stat["weighted_mastery"] / max(stat["weight"], 1), 3),
            "attempts": stat["attempts"],
            "wrong_count": stat["wrong_count"],
        }
        for topic, stat in weak_topic_stats.items()
    ], key=lambda item: (item["p_mastery"], -item["wrong_count"]))[:6]

    quiz_attempts = db.execute(
        "SELECT COUNT(*) AS count FROM quiz_records WHERE student_id = ?",
        (student_id,),
    ).fetchone()["count"]
    note_count = db.execute(
        "SELECT COUNT(*) AS count FROM learning_events WHERE student_id = ? AND event_type = 'note_added'",
        (student_id,),
    ).fetchone()["count"]

    generated_types = set(generated_resources)
    engaged_types = set(resource_engagement)
    has_explanation_material = bool({"lecture", "ppt", "lab"} & (generated_types | engaged_types))
    suggestions = []
    if has_explanation_material and not quiz_attempts:
        suggestions.append("学生已经接触解释型材料，但还没有形成答题诊断，下一轮优先安排练习题。")
    if (generated_resources.get("lecture", {}).get("count", 0) or resource_engagement.get("lecture", {}).get("events", 0)) and not generated_resources.get("lab"):
        suggestions.append("学生已经看过课程讲解，但缺少可操作验证，适合补一个实操案例。")
    if note_count:
        suggestions.append("学生做过笔记，生成内容应承接笔记中的关注点，避免重复泛泛解释。")
    if weak_topics:
        suggestions.append("优先围绕掌握度较低或错题较多的知识点安排补救和复习。")

    return {
        "student_id": student_id,
        "user_id": resolved_user,
        "session_activity": {
            "total_seconds": int(activity_row["total_seconds"] or 0),
            "today_seconds": int(activity_row["today_seconds"] or 0),
            "week_seconds": int(activity_row["week_seconds"] or 0),
        },
        "user_activity": user_activity,
        "generated_resources": generated_resources,
        "resource_engagement": resource_engagement,
        "recent_events": recent_events,
        "note_count": int(note_count or 0),
        "note_previews": note_previews[:5],
        "quiz_attempts": int(quiz_attempts or 0),
        "weak_topics": weak_topics,
        "planner_suggestions": suggestions[:5],
    }


def get_user_space(user_id: str) -> dict:
    resolved_user = ensure_user(user_id)
    db = get_db()
    user_ids = _local_user_ids(resolved_user)
    session_ids = _owned_sessions(resolved_user)
    params = session_ids
    placeholders = _placeholders(session_ids)

    totals = {
        "sessions": len(session_ids),
        "resources": 0,
        "quiz_attempts": 0,
        "correct_answers": 0,
        "learning_seconds": 0,
        "today_learning_seconds": 0,
        "total_tokens": 0,
        "estimated_tokens": 0,
    }
    profiles = []
    mastery_rows = []
    recent_sessions = []

    if session_ids:
        totals["resources"] = db.execute(
            f"SELECT COUNT(*) AS count FROM resources WHERE student_id IN ({placeholders})",
            params,
        ).fetchone()["count"]
        quiz_totals = db.execute(
            f"SELECT COUNT(*) AS attempts, COALESCE(SUM(is_correct), 0) AS correct "
            f"FROM quiz_records WHERE student_id IN ({placeholders})",
            params,
        ).fetchone()
        totals["quiz_attempts"] = quiz_totals["attempts"]
        totals["correct_answers"] = quiz_totals["correct"]
        mastery_rows = [
            dict(row) for row in db.execute(
                f"SELECT topic, score, p_mastery, attempts, correct_count, wrong_count, "
                f"review_count, last_reviewed_at, updated_at "
                f"FROM mastery_states WHERE student_id IN ({placeholders}) ORDER BY updated_at DESC",
                params,
            ).fetchall()
        ]
        profile_rows = db.execute(
            f"SELECT p.profile_json FROM profiles p JOIN ("
            f"SELECT student_id, MAX(version) AS version FROM profiles "
            f"WHERE student_id IN ({placeholders}) GROUP BY student_id"
            f") latest ON latest.student_id = p.student_id AND latest.version = p.version",
            params,
        ).fetchall()
        for row in profile_rows:
            try:
                profiles.append(json.loads(row["profile_json"]))
            except (TypeError, json.JSONDecodeError):
                continue
        recent_sessions = [
            dict(row) for row in db.execute(
                f"SELECT s.id AS student_id, "
                f"SUBSTR(COALESCE((SELECT content FROM conversations c WHERE c.student_id=s.id AND c.role='user' ORDER BY c.timestamp DESC LIMIT 1), '未命名会话'), 1, 54) AS title, "
                f"COALESCE((SELECT MAX(timestamp) FROM conversations c WHERE c.student_id=s.id), s.created_at) AS updated_at, "
                f"(SELECT COUNT(*) FROM resources r WHERE r.student_id=s.id) AS resource_count "
                f"FROM students s WHERE s.id IN ({placeholders}) ORDER BY updated_at DESC LIMIT 6",
                params,
            ).fetchall()
        ]

    activity = db.execute(
        f"SELECT COALESCE(SUM(active_seconds), 0) AS seconds FROM learning_activity "
        f"WHERE user_id IN ({_placeholders(user_ids)})",
        user_ids,
    ).fetchone()
    totals["learning_seconds"] = activity["seconds"]
    contributions = _daily_contributions(db, user_ids)
    totals["today_learning_seconds"] = contributions["days"][-1]["seconds"]
    totals["active_days"] = contributions["active_days"]
    totals["current_streak"] = contributions["current_streak"]
    usage = db.execute(
        "SELECT COALESCE(SUM(total_tokens), 0) AS total, "
        "COALESCE(SUM(CASE WHEN estimated=1 THEN total_tokens ELSE 0 END), 0) AS estimated "
        f"FROM token_usage WHERE user_id IN ({_placeholders(user_ids)})",
        user_ids,
    ).fetchone()
    totals["total_tokens"] = usage["total"]
    totals["estimated_tokens"] = usage["estimated"]
    totals["accuracy"] = round(
        totals["correct_answers"] / totals["quiz_attempts"] * 100, 1
    ) if totals["quiz_attempts"] else 0

    topic_stats = defaultdict(lambda: {"weighted_score": 0.0, "weight": 0, "attempts": 0, "review_count": 0})
    for row in mastery_rows:
        weight = max(int(row["attempts"] or 0), 1)
        topic = normalize_topic(row["topic"], row["topic"] or "当前知识点")
        stat = topic_stats[topic]
        mastery_val = float(row["p_mastery"] or row["score"] or 0.5)
        stat["weighted_score"] += mastery_val * weight
        stat["weight"] += weight
        stat["attempts"] += int(row["attempts"] or 0)
        stat["review_count"] = max(stat["review_count"], int(row["review_count"] or 0))
    topics = [
        {
            "topic": topic,
            "score": round(stat["weighted_score"] / stat["weight"] * 100),
            "attempts": stat["attempts"],
            "review_count": stat["review_count"],
        }
        for topic, stat in topic_stats.items()
    ]
    topics.sort(key=lambda item: (item["score"], -item["attempts"]))

    base_scores = [
        LEVEL_SCORE.get(_summary_value(profile.get("知识基础")), 2)
        for profile in profiles
    ]
    base_score = round(sum(base_scores) / len(base_scores)) if base_scores else 2
    preferences = Counter(
        _summary_value(profile.get("认知偏好")) for profile in profiles if _summary_value(profile.get("认知偏好"))
    )
    courses = Counter(
        _summary_value(profile.get("当前课程")) for profile in profiles if _summary_value(profile.get("当前课程"))
    )
    mastery_score = round(sum(item["score"] for item in topics) / len(topics)) if topics else base_score * 28
    agent_loop = _build_agent_loop(totals, topics, recent_sessions, db, session_ids)
    knowledge_graph = _build_knowledge_graph(topics)

    return {
        "user_id": resolved_user,
        "display_name": "NetMind 学习者",
        "totals": totals,
        "overall_profile": {
            "ability_score": max(0, min(mastery_score, 100)),
            "base_level": LEVEL_NAME.get(base_score, "基础中等"),
            "primary_preference": preferences.most_common(1)[0][0] if preferences else "图解与示例",
            "courses": [item[0] for item in courses.most_common(4)],
            "weak_topics": topics[:4],
            "strong_topics": sorted(topics, key=lambda item: (-item["score"], -item["attempts"]))[:4],
        },
        "mastery_topics": topics,
        "agent_loop": agent_loop,
        "knowledge_graph": knowledge_graph,
        "recent_sessions": recent_sessions,
        "contributions": contributions,
    }
