import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from db.database import get_db
from agents.topic_utils import normalize_topic
from services.llm_client import LLM_MAX_OUTPUT_TOKENS, chat
from services.user_space import get_learning_signal_summary
from services.user_memory import get_active_memory_text
from bkt import BKTParams, compute_mastery, get_guess_rate


ALLOWED_TOOLS = ("lecture", "quiz", "mindmap", "ppt", "interactive", "lab", "path", "assessment")
PRODUCT_HINTS = {
    "lecture": ["讲解", "解释", "原理", "概念", "不懂", "讲一下", "图解", "演示", "动画", "视频"],
    "quiz": ["练习", "测试题", "试题", "题库", "刷题", "出题", "考考我"],
    "mindmap": ["思维导图", "知识结构", "脉络", "框架", "关系图", "梳理"],
    "ppt": ["ppt", "html课件", "html 课件", "幻灯片", "课件", "导出"],
    "interactive": ["互动教学", "交互教学", "交互动画", "互动网页", "交互网页", "交互式网页", "互动页面", "交互页面", "可交互html", "可交互的html", "可交互网页", "可交互的网页", "互动html", "interactive html", "interactive webpage"],
    "lab": ["实操案例", "实验任务书", "实验指导", "实操指导", "实操练习"],
    "path": ["学习计划", "学习路径", "学习规划", "怎么学"],
    "assessment": ["评估报告", "测评报告", "诊断报告", "薄弱分析"],
}
EXPLICIT_PRODUCT_TOOLS = ("interactive", "lecture", "ppt", "lab", "path", "assessment", "quiz", "mindmap")

PLANNER_PROMPT = """你为自主学习者选择资源；未指定产物时使用lecture，其余按用户明确要求生成。
工具：lecture讲解、mindmap思维导图、ppt PPT课件、interactive互动教学（交互动画）、lab实操、quiz练习、path学习路径、assessment评估。
练习、路径和评估不自动安排；“用互动教学讲解”只需interactive，另要文字文档时再加lecture。
输出JSON：{"goal":"目标","focus_topics":[],"strategy":"简短理由","actions":[{"tool":"lecture","reason":"理由","instruction":"具体需求"}]}"""


@dataclass
class TutorPlan:
    goal: str
    focus_topics: list[str]
    strategy: str
    actions: list[dict]
    source: str = "model"

    @property
    def intents(self) -> list[str]:
        return [action["tool"] for action in self.actions]

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "focus_topics": self.focus_topics,
            "strategy": self.strategy,
            "actions": self.actions,
            "source": self.source,
        }


def _extract_json(value: str) -> dict:
    text = str(value or "").strip()
    candidates = [text]
    if text.startswith("```"):
        candidates.append(text.split("\n", 1)[1].rsplit("```", 1)[0].strip())
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{"):text.rfind("}") + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def get_mastery_state(student_id: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT topic, score, p_mastery, attempts, correct_count, wrong_count, "
        "last_action, review_count, last_reviewed_at, updated_at "
        "FROM mastery_states WHERE student_id = ? ORDER BY p_mastery ASC, attempts DESC LIMIT 12",
        (student_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _recent_records(student_id: str, limit: int = 6) -> list[dict]:
    rows = get_db().execute(
        "SELECT qr.is_correct, qr.user_answer, q.question_json "
        "FROM quiz_records qr JOIN quizzes q ON qr.quiz_id = q.id "
        "WHERE qr.student_id = ? ORDER BY qr.timestamp DESC LIMIT ?",
        (student_id, limit),
    ).fetchall()
    records = []
    for row in rows:
        question = json.loads(row["question_json"])
        records.append({
            "topic": question.get("topic") or question.get("knowledge_point") or "当前知识点",
            "question": question.get("question", ""),
            "is_correct": bool(row["is_correct"]),
            "user_answer": row["user_answer"],
        })
    return records


def _behavior_instruction_suffix(signals: dict | None) -> str:
    if not signals:
        return ""
    lines = []
    session_activity = signals.get("session_activity") or {}
    if session_activity.get("today_seconds") or session_activity.get("total_seconds"):
        lines.append(
            f"本会话学习：今日 {session_activity.get('today_seconds', 0)} 秒，"
            f"累计 {session_activity.get('total_seconds', 0)} 秒。"
        )

    engagement = signals.get("resource_engagement") or {}
    if engagement:
        viewed = [
            f"{resource_type}({item.get('events', 0)}次/{item.get('seconds', 0)}秒)"
            for resource_type, item in engagement.items()
        ]
        lines.append(f"最近浏览资源：{', '.join(viewed[:6])}。")

    generated = signals.get("generated_resources") or {}
    if generated:
        generated_text = [
            f"{resource_type}x{item.get('count', 0)}"
            for resource_type, item in generated.items()
        ]
        lines.append(f"已生成资源：{', '.join(generated_text[:6])}。")

    notes = signals.get("note_previews") or []
    if notes:
        lines.append("相关笔记：" + "；".join(notes[:3]))

    if not lines:
        return ""
    return (
        "\n\n相关学习记录：\n- "
        + "\n- ".join(lines)
        + "\n记录用于承接话题，不代表掌握程度。"
    )


def _enrich_plan_with_behavior(plan: TutorPlan, signals: dict | None) -> TutorPlan:
    suffix = _behavior_instruction_suffix(signals)
    if not suffix:
        return plan

    tool_specific = {
        "lecture": "结合相关历史回应当前问题。",
        "interactive": "围绕问题制作互动教学，用交互动画呈现变化。",
        "quiz": "按用户要求出题，给出答案与解析。",
        "lab": "提供围绕当前问题的实操示例。",
        "ppt": "按用户需求制作PPT课件。",
        "mindmap": "呈现相关概念之间的关系。",
        "path": "结合用户目标规划学习路径。",
        "assessment": "依据现有记录回应评估需求。",
    }
    actions = []
    for action in plan.actions:
        tool = action.get("tool")
        behavior_note = tool_specific.get(tool, "")
        actions.append({
            **action,
            "instruction": (
                str(action.get("instruction") or "")
                + suffix
                + (f"\n{behavior_note}" if behavior_note else "")
            ).strip(),
        })
    return TutorPlan(
        goal=plan.goal,
        focus_topics=plan.focus_topics,
        strategy=(plan.strategy or "") + " 已结合相关学习记录。",
        actions=actions,
        source=f"{plan.source}+behavior",
    )


def _keyword_fallback(message: str, profile: dict, mastery: list[dict], signals: dict | None = None) -> TutorPlan:
    lowered = _without_negated_requests(message)
    explicit = _explicit_product_tools(message)
    focus = list(profile.get("薄弱知识点", []) or [])
    focus.extend(item["topic"] for item in mastery if (item.get("p_mastery") or item.get("score", 1)) < 0.6)
    focus = list(dict.fromkeys(item for item in focus if item))[:4]
    tools = explicit[:4] if explicit else ["lecture"]
    return TutorPlan(
        goal=f"围绕{focus[0] if focus else '当前知识点'}完成一次理解与检验",
        focus_topics=focus,
        strategy="围绕当前问题生成最少且必要的学习内容。",
        actions=[
            {"tool": tool, "reason": "满足本轮学习目标", "instruction": message}
            for tool in list(dict.fromkeys(tools))
        ],
        source="fallback",
    )


def _without_negated_requests(message: str) -> str:
    lowered = (message or "").lower()
    # Negation scopes over coordinated products too: “不要路径或评估”.
    # Keep a following positive instruction such as “只要讲解”.
    return re.sub(
        r"(?:不需要|不要|无需|不用|别|不必)(?:额外)?[^，。,；;\n]*?"
        r"(?=(?:只要|只需|但是|但|改为|而是)|[，。,；;\n]|$)",
        "", lowered,
    )


def _explicit_product_tools(message: str) -> list[str]:
    lowered = _without_negated_requests(message)
    interactive = _has_interactive_request(lowered)
    if interactive:
        return ['interactive', *_interactive_companion_tools(lowered)]
    requested = []
    for tool in EXPLICIT_PRODUCT_TOOLS:
        positions = [lowered.find(hint) for hint in PRODUCT_HINTS[tool] if hint in lowered]
        if tool == 'quiz':
            # “主题/问题” and goals such as “理解/巩固” do not authorize exercises.
            # Retain requests expressed through a quantity or a generation verb.
            match = re.search(r'(?:[0-9一二两三四五六七八九十几]+\s*道\s*题|(?:给我|帮我|请)?\s*(?:出|做|生成|提供)\s*(?:一些|一组|一套|几道|[0-9一二两三四五六七八九十]+道)?\s*(?:选择|判断|填空|计算|应用)?题)', lowered)
            if match:
                positions.append(match.start())
        if tool in {'lab', 'path', 'assessment'}:
            nouns = {
                'lab': r'实验|实操|实践任务|抓包',
                'path': r'计划|路径|规划|安排',
                'assessment': r'评估|测评|诊断|掌握情况',
            }
            match = re.search(r'(?:请|帮我|给我|为我)?\s*(?:生成|制作|设计|提供|制定|做|安排)\s*(?:一份|一个|一套|我的|学习)?\s*(?:' + nouns[tool] + r')', lowered)
            if match:
                positions.append(match.start())
        if positions:
            requested.append((min(positions), tool))
    return [tool for _, tool in sorted(requested)]


def _has_interactive_request(message: str) -> bool:
    lowered = _without_negated_requests(message)
    if any(hint in lowered for hint in PRODUCT_HINTS['interactive']):
        return True
    return bool(re.search(r'(?:可交互|交互式|互动式)\s*(?:的\s*)?(?:html|网页|页面)|(?:html|网页|页面).{0,12}(?:能调节|可调节|可以调节|支持交互)', lowered))


def _interactive_companion_tools(message: str, standalone: bool = False) -> list[str]:
    """A webpage's explanation/diagram is not a second requested artifact."""
    lowered = _without_negated_requests(message)
    products = {
        'lecture': r'(?:文字讲解|讲解文档|讲解笔记|文字解释|文字说明)',
        'ppt': r'(?:ppt|课件|幻灯片)',
        'mindmap': r'(?:思维导图|知识导图)',
        'quiz': r'(?:练习题|题库|测试题|道题|出题|刷题)',
        'lab': r'(?:实操案例|实验任务书|实操指导)',
        'path': r'(?:学习路径|学习计划)',
        'assessment': r'(?:评估报告|测评报告|诊断报告)',
    }
    result = []
    for tool, noun in products.items():
        # Coordinated named artifacts count; incidental "解释/图解/操作" does not.
        prefix = r'(?:另外|另|同时|并且|并|以及|和|及|与|再|还要|再要|也要)\s*(?:给|做|生成|制作|提供|输出|一份|一个|一张|我|份|张|套|[0-9一二三四五六七八九十]+)*\s*'
        before_webpage = noun + r'\s*(?:和|及|与|以及|并(?:生成|制作)?|同时(?:生成|制作)?)\s*(?:一份|一个)?(?:互动教学|交互教学|交互动画|互动网页|交互网页|交互式网页|可交互html)'
        separate_clause = any(
            re.search(noun, clause) and not _has_interactive_request(clause)
            and re.search(r'给|生成|制作|做|提供|输出|出', clause)
            and not re.search(r'其中|里面|网页内|页面内|页面中|内含|包含|在页面|在网页|放在|嵌入|页面上|网页上', clause)
            for clause in re.split(r'[，。,；;\n]', lowered)
        )
        if re.search(prefix + noun, lowered) or re.search(before_webpage, lowered) or separate_clause or standalone and re.search(noun, lowered):
            result.append(tool)
    return result


def _explicit_plan(message: str, profile: dict, tools: list[str]) -> TutorPlan:
    focus = list(profile.get("薄弱知识点", []) or [])[:4]
    return TutorPlan(
        goal=f"生成学生明确需要的{'、'.join(tools)}学习产物",
        focus_topics=focus,
        strategy="学生已经明确指定产物，直接调用对应工具，避免增加一次规划模型等待。",
        actions=[
            {
                "tool": tool,
                "reason": "学生明确指定该学习产物",
                "instruction": message,
            }
            for tool in tools
        ],
        source="explicit",
    )


def _selected_product_companions(message: str) -> list[str]:
    """Selected UI products own their explanatory content, not extra artifacts."""
    products = {
        'lecture': r'文字讲解|讲解文档|讲解笔记|文字解释|文字说明',
        'ppt': r'ppt|课件|幻灯片',
        'interactive': r'互动教学|交互教学|交互动画|互动网页|交互网页|交互式网页|可交互html',
        'mindmap': r'思维导图|知识导图',
        'quiz': r'练习题|题库|测试题|道题|出题|刷题|考考我',
        'lab': r'实操案例|实验任务书|实操指导',
        'path': r'学习路径|学习计划',
        'assessment': r'评估报告|测评报告|诊断报告',
    }
    clauses = re.split(r'[，。,；;\n]', _without_negated_requests(message))
    return [tool for tool, noun in products.items() if any(
        (re.search(noun, clause) or tool in {'quiz', 'lab', 'path', 'assessment'} and tool in _explicit_product_tools(clause))
        and not re.search(r'其中|里面|内含|包含|嵌入|放在|(?:ppt|课件|网页|页面|文档)(?:内|中|里|上)|在(?:ppt|课件|网页|页面|文档)', clause)
        for clause in clauses
    )]


def _merge_required_tools(
    plan: TutorPlan,
    required_tools: list[str] | None,
    message: str,
) -> TutorPlan:
    normalized = []
    for tool in required_tools or []:
        name = str(tool or "").strip().lower()
        if name in ALLOWED_TOOLS and name not in normalized:
            normalized.append(name)
    if not normalized:
        return plan

    existing_by_tool = {
        action.get("tool"): action
        for action in plan.actions
        if action.get("tool") in ALLOWED_TOOLS
    }
    required_actions = []
    for tool in normalized:
        existing = existing_by_tool.get(tool, {})
        required_actions.append({
            "tool": tool,
            "reason": "用户在界面中指定本次必须生成该学习资源",
            "instruction": str(existing.get("instruction") or message),
        })

    max_actions = max(4, len(required_actions))
    inferred_actions = [
        action
        for action in plan.actions
        if action.get("tool") not in normalized
    ][:max_actions - len(required_actions)]
    selected_names = "、".join(normalized)
    return TutorPlan(
        goal=plan.goal,
        focus_topics=plan.focus_topics,
        strategy=f"优先保证界面指定的 {selected_names}；其余资源继续由 Agent 根据学习需求推断。{plan.strategy}",
        actions=[*required_actions, *inferred_actions],
        source=f"{plan.source}+required",
    )


def _respect_requested_products(plan: TutorPlan, message: str, required_tools: list[str] | None) -> TutorPlan:
    """Optional exercises and reports require an explicit request, including planner fallback."""
    normalized_required = {str(tool).strip().lower() for tool in required_tools or [] if str(tool).strip().lower() in ALLOWED_TOOLS}
    requested = set(_explicit_product_tools(message)) | normalized_required
    opt_in = {"quiz", "ppt", "interactive", "lab", "assessment", "path"}
    actions = [action for action in plan.actions if action.get("tool") not in opt_in or action["tool"] in requested]
    if normalized_required and 'interactive' not in requested:
        allowed = normalized_required | set(_selected_product_companions(message))
        actions = [action for action in actions if action.get('tool') in allowed]
    if 'interactive' in requested:
        standalone = 'interactive' in normalized_required and not _has_interactive_request(message)
        allowed = {'interactive', *_interactive_companion_tools(message, standalone=standalone), *normalized_required}
        actions = [action for action in actions if action.get('tool') in allowed]
    if not actions:
        tool = 'interactive' if 'interactive' in requested else 'lecture'
        actions = [{"tool": tool, "reason": "满足当前明确要求的学习产物", "instruction": message}]
    return TutorPlan(plan.goal, plan.focus_topics, plan.strategy, actions, plan.source)


def plan_tutor_actions(
    student_id: str,
    message: str,
    profile: dict,
    user_id: str | None = None,
    required_tools: list[str] | None = None,
) -> TutorPlan:
    mastery = []  # Historical scores do not schedule autonomous learning tasks.
    records = _recent_records(student_id)
    signals = {key: value for key, value in get_learning_signal_summary(student_id, user_id).items()
               if key not in {"weak_topics", "planner_suggestions"}}
    permanent_memory = get_active_memory_text(user_id)

    def apply_memory(plan: TutorPlan) -> TutorPlan:
        plan = _respect_requested_products(plan, message, required_tools)
        if not permanent_memory:
            return plan
        for action in plan.actions:
            action["instruction"] = (
                f"{action.get('instruction') or message}\n\n"
                "用户长期偏好："
                f"{permanent_memory}"
            )
        return plan

    explicit_tools = _explicit_product_tools(message)
    normalized_required = [str(tool).strip().lower() for tool in required_tools or []]
    if 'interactive' in normalized_required:
        # A selected resource button already expresses intent; no planning model is
        # needed for a plain question. Preserve only other named requested artifacts.
        if not _has_interactive_request(message):
            explicit_tools = ['interactive', *_interactive_companion_tools(message, standalone=True)]
        plan = _merge_required_tools(_explicit_plan(message, profile, explicit_tools), required_tools, message)
        return apply_memory(_enrich_plan_with_behavior(plan, signals))
    if 'ppt' in normalized_required:
        # Selecting PPT already expresses the product intent. Keep any explicitly
        # requested companions without asking a model to invent additional work.
        selected_tools = list(dict.fromkeys([*explicit_tools, *normalized_required]))
        plan = _merge_required_tools(_explicit_plan(message, profile, selected_tools), required_tools, message)
        return apply_memory(_enrich_plan_with_behavior(plan, signals))
    if explicit_tools:
        plan = _merge_required_tools(
            _explicit_plan(message, profile, explicit_tools),
            required_tools,
            message,
        )
        return apply_memory(_enrich_plan_with_behavior(plan, signals))
    prompt = (
        f"问题：{message}\n\n"
        f"背景：{json.dumps(profile, ensure_ascii=False)}\n\n"
        f"答题记录：{json.dumps(records, ensure_ascii=False)}\n\n"
        f"相关记录：{json.dumps(signals, ensure_ascii=False)}"
    )
    if permanent_memory:
        prompt += f"\n\n长期偏好：{permanent_memory}"
    try:
        result = chat(
            [
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=LLM_MAX_OUTPUT_TOKENS,
        )
        parsed = _extract_json(result)
        actions = []
        for item in parsed.get("actions", []):
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool", "")).strip().lower()
            if tool not in ALLOWED_TOOLS or any(action["tool"] == tool for action in actions):
                continue
            actions.append({
                "tool": tool,
                "reason": str(item.get("reason") or "服务当前学习目标"),
                "instruction": str(item.get("instruction") or message),
            })
            if len(actions) >= 4:
                break
        if not actions:
            raise ValueError("planner returned no valid actions")
        focus = parsed.get("focus_topics") if isinstance(parsed.get("focus_topics"), list) else []
        plan = TutorPlan(
            goal=str(parsed.get("goal") or "完成本轮学习任务"),
            focus_topics=[str(item) for item in focus[:5]],
            strategy=str(parsed.get("strategy") or "根据画像与答题记录选择学习动作。"),
            actions=actions,
        )
        return apply_memory(_enrich_plan_with_behavior(
            _merge_required_tools(plan, required_tools, message),
            signals,
        ))
    except Exception:
        plan = _merge_required_tools(
            _keyword_fallback(message, profile, mastery, signals),
            required_tools,
            message,
        )
        return apply_memory(_enrich_plan_with_behavior(plan, signals))


def reflect_resource(resource: dict, action: dict) -> dict:
    content = str(resource.get("content") or "").strip()
    resource_type = resource.get("type", action.get("tool", "resource"))
    issues = []
    if not content:
        issues.append("输出为空")
    if resource_type == "quiz":
        questions = resource.get("metadata", {}).get("questions")
        if questions is None:
            try:
                questions = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                questions = None
        if isinstance(questions, dict):
            questions = questions.get('questions')
        count_match = re.search(r"(\d+)\s*道", str(action.get("instruction") or ""))
        if not isinstance(questions, list) or not questions:
            issues.append("练习题格式无效或题目为空")
            questions = []
        if count_match and len(questions) < int(count_match.group(1)):
            issues.append("练习题数量少于用户要求")
        if any(not isinstance(item, dict) or not item.get("question") or item.get("answer") in (None, '', [], {}) for item in questions):
            issues.append("存在缺少题干或答案的题目")
    elif resource_type == "mindmap" and "mindmap" not in content.lower():
        issues.append("思维导图格式缺失")
    elif resource_type == "ppt" and not (re.search(r'<html\b', content, re.I) and re.search(r'</html\s*>', content, re.I)):
        issues.append("课件HTML文档不完整")
    return {
        "tool": action.get("tool"),
        "passed": not issues,
        "issues": issues,
        "decision": "accept" if not issues else "repair_once",
    }


def save_tutor_run(student_id: str, request: str, plan: TutorPlan, reflections: list[dict]) -> int:
    cursor = get_db().execute(
        "INSERT INTO tutor_runs (student_id, request, plan_json, reflection_json) VALUES (?, ?, ?, ?)",
        (
            student_id,
            request,
            json.dumps(plan.to_dict(), ensure_ascii=False),
            json.dumps(reflections, ensure_ascii=False),
        ),
    )
    get_db().commit()
    return cursor.lastrowid


def update_mastery_from_quiz(student_id: str, question: dict, is_correct: bool) -> dict:
    topic = normalize_topic(
        question.get("topic")
        or question.get("knowledge_point")
        or question.get("related_topic")
        or question.get("question")
        or "当前知识点",
        "当前知识点",
    )[:42]
    question_type = str(question.get("type") or "choice")

    db = get_db()
    current = db.execute(
        "SELECT p_mastery, p_init, p_transit, p_guess, p_slip, decay_rate, "
        "score, attempts, correct_count, wrong_count, review_count, last_reviewed_at "
        "FROM mastery_states WHERE student_id = ? AND topic = ?",
        (student_id, topic),
    ).fetchone()

    if current:
        p_mastery = float(current["p_mastery"] or current["score"] or 0.3)
        attempts = int(current["attempts"]) + 1
        correct_count = int(current["correct_count"]) + (1 if is_correct else 0)
        wrong_count = int(current["wrong_count"]) + (0 if is_correct else 1)
        review_count = int(current["review_count"] or 0) + 1
        last_reviewed_at = current["last_reviewed_at"]
        params = BKTParams(
            p_init=float(current["p_init"] or 0.3),
            p_transit=float(current["p_transit"] or 0.1),
            p_guess=float(current["p_guess"] or get_guess_rate(question_type)),
            p_slip=float(current["p_slip"] or 0.1),
            decay_rate=float(current["decay_rate"] or 0.05),
        )
    else:
        p_mastery = 0.3
        attempts = 1
        correct_count = 1 if is_correct else 0
        wrong_count = 0 if is_correct else 1
        review_count = 1
        last_reviewed_at = None
        params = BKTParams(p_guess=get_guess_rate(question_type))

    # 计算距上次复习的小时数
    hours_since_last = 0.0
    if last_reviewed_at:
        try:
            last_dt = datetime.fromisoformat(str(last_reviewed_at))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            hours_since_last = max(0, (now - last_dt).total_seconds() / 3600)
        except (ValueError, TypeError):
            hours_since_last = 0.0

    # BKT + 遗忘曲线计算
    result = compute_mastery(
        p_mastery=p_mastery,
        is_correct=is_correct,
        question_type=question_type,
        hours_since_last_review=hours_since_last,
        review_count=review_count,
        params=params,
    )
    new_p_mastery = result["p_mastery"]
    action = result["action"]
    message = result["message"]

    # 兼容旧 score 字段（同步更新）
    score = new_p_mastery

    db.execute(
        "INSERT INTO mastery_states "
        "(student_id, topic, p_mastery, p_init, p_transit, p_guess, p_slip, decay_rate, "
        "score, attempts, correct_count, wrong_count, review_count, last_reviewed_at, "
        "last_action, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(student_id, topic) DO UPDATE SET "
        "p_mastery=excluded.p_mastery, score=excluded.score, "
        "attempts=excluded.attempts, correct_count=excluded.correct_count, "
        "wrong_count=excluded.wrong_count, review_count=excluded.review_count, "
        "last_reviewed_at=CURRENT_TIMESTAMP, last_action=excluded.last_action, "
        "updated_at=CURRENT_TIMESTAMP",
        (student_id, topic, new_p_mastery, params.p_init, params.p_transit,
         params.p_guess, params.p_slip, params.decay_rate,
         score, attempts, correct_count, wrong_count, review_count, action),
    )
    db.commit()
    return {
        "topic": topic,
        "p_mastery": new_p_mastery,
        "score": round(score, 2),
        "attempts": attempts,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "review_count": review_count,
        "hours_since_last_review": round(hours_since_last, 1),
        "action": action,
        "message": message,
    }


def build_followup_plan(mastery: dict) -> TutorPlan:
    """Turn a mastery decision into a small, bounded teaching loop."""
    topic = mastery.get("topic") or "当前知识点"
    action = mastery.get("action") or "retry"
    if action == "remediate":
        actions = [
            {
                "tool": "lecture",
                "reason": "连续答错后先换角度补讲",
                "instruction": (
                    f"换个角度解释「{topic}」，回应这次答题中的疑问。"
                ),
            },
            {
                "tool": "quiz",
                "reason": "用降阶诊断题确认卡点是否消除",
                "instruction": (
                    f"围绕「{topic}」提供相关练习与答案解析。"
                ),
            },
        ]
        strategy = "连续答错触发补救环：先换角度补讲，再用降阶题确认理解。"
    elif action == "reinforce":
        actions = [{
            "tool": "quiz",
            "reason": "答对后用迁移题确认不是偶然命中",
            "instruction": (
                f"围绕「{topic}」提供不同情境的练习与答案解析。"
            ),
        }]
        strategy = "当前回答正确，但掌握度还未稳定，用迁移题做一次巩固。"
    elif action == "retry":
        actions = [{
            "tool": "quiz",
            "reason": "首次答错后用同类变式定位卡点",
            "instruction": (
                f"围绕「{topic}」提供同类练习，说明解答要点。"
            ),
        }]
        strategy = "首次答错先做小步重试，避免过早进入大段补讲。"
    else:
        actions = []
        strategy = "掌握度已经达到推进阈值，建议进入下一个知识点。"

    return TutorPlan(
        goal=f"根据答题反馈继续巩固「{topic}」",
        focus_topics=[topic],
        strategy=strategy,
        actions=actions,
        source="mastery_loop",
    )
