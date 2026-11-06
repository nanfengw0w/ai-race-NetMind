"""
Bayesian Knowledge Tracing (BKT) with Forgetting Curve

BKT 模型：追踪学生对某个知识点"是否掌握"的概率。
每次答题后用贝叶斯公式更新 P(掌握)。

遗忘曲线：基于 Ebbinghaus 模型，随时间推移 P(掌握) 自然衰减。
复习次数越多，衰减越慢。

核心公式：
  答对后：P(L|correct) = P(L) * (1 - P(S)) / [P(L) * (1 - P(S)) + (1 - P(L)) * P(G)]
  答错后：P(L|wrong)   = P(L) * P(S) / [P(L) * P(S) + (1 - P(L)) * (1 - P(G))]
  学习后：P(L') = P(L|obs) + (1 - P(L|obs)) * P(T)
  遗忘后：P(L_decayed) = P(L) * exp(-lambda * t)
"""

import math
from dataclasses import dataclass


@dataclass
class BKTParams:
    p_init: float = 0.3      # 初始掌握概率
    p_transit: float = 0.1    # 学习概率（不会→会）
    p_guess: float = 0.25     # 猜对概率
    p_slip: float = 0.1       # 失误概率
    decay_rate: float = 0.05  # 遗忘衰减系数（每小时）


DEFAULT_PARAMS = BKTParams()

# 不同题型的默认猜对概率
GUESS_BY_TYPE = {
    "choice": 0.25,
    "multi": 0.125,
    "judge": 0.5,
    "fill": 0.05,
    "short": 0.05,
}


def get_guess_rate(question_type: str) -> float:
    """根据题型返回合理的猜对概率"""
    normalized = (question_type or "").lower().replace("-", "_")
    for key, rate in GUESS_BY_TYPE.items():
        if key in normalized:
            return rate
    return DEFAULT_PARAMS.p_guess


def bkt_update(p_mastery: float, is_correct: bool, params: BKTParams) -> float:
    """
    单次 BKT 更新。

    Args:
        p_mastery: 当前掌握概率 P(L)
        is_correct: 本次答题是否正确
        params: BKT 参数

    Returns:
        更新后的掌握概率
    """
    p_l = max(0.001, min(0.999, p_mastery))
    p_g = params.p_guess
    p_s = params.p_slip
    p_t = params.p_transit

    if is_correct:
        # P(L|correct) = P(L)*(1-P(S)) / [P(L)*(1-P(S)) + (1-P(L))*P(G)]
        numerator = p_l * (1 - p_s)
        denominator = numerator + (1 - p_l) * p_g
    else:
        # P(L|wrong) = P(L)*P(S) / [P(L)*P(S) + (1-P(L))*(1-P(G))]
        numerator = p_l * p_s
        denominator = numerator + (1 - p_l) * (1 - p_g)

    p_posterior = numerator / denominator if denominator > 0 else p_l

    # 学习效应：未掌握的部分有概率学会
    p_new = p_posterior + (1 - p_posterior) * p_t

    return max(0.001, min(0.999, p_new))


def apply_forgetting(p_mastery: float, hours_elapsed: float, params: BKTParams,
                     review_count: int = 0) -> float:
    """
    遗忘曲线衰减。

    复习次数越多，衰减越慢（lambda 随 review_count 递减）。
    lambda = decay_rate / (1 + 0.3 * review_count)

    Args:
        p_mastery: 当前掌握概率
        hours_elapsed: 距上次学习的小时数
        params: BKT 参数
        review_count: 复习次数

    Returns:
        衰减后的掌握概率
    """
    if hours_elapsed <= 0:
        return p_mastery

    # 衰减率随复习次数递减
    effective_lambda = params.decay_rate / (1 + 0.3 * min(review_count, 20))
    decay_factor = math.exp(-effective_lambda * hours_elapsed)

    # 不完全遗忘到0，保留一个下限
    p_decayed = p_mastery * decay_factor
    return max(0.05, p_decayed)


def compute_mastery(p_mastery: float, is_correct: bool, question_type: str,
                    hours_since_last_review: float, review_count: int,
                    params: BKTParams | None = None) -> dict:
    """
    完整的掌握度计算流程：先衰减，再用BKT更新。

    Returns:
        dict with p_mastery, action, message
    """
    if params is None:
        params = BKTParams(p_guess=get_guess_rate(question_type))

    # 1. 先应用遗忘衰减
    if hours_since_last_review > 0.5:
        p_mastery = apply_forgetting(p_mastery, hours_since_last_review, params, review_count)

    # 2. 用 BKT 更新
    p_new = bkt_update(p_mastery, is_correct, params)

    # 3. 根据掌握概率决定教学动作
    if p_new >= 0.8:
        action = "advance"
        message = "掌握度已达标，可以进入下一个知识点。"
    elif is_correct and p_new >= 0.6:
        action = "reinforce"
        message = "回答正确，建议再做一道迁移题巩固。"
    elif not is_correct and p_new >= 0.4:
        action = "retry"
        message = "有一定基础但存在卡点，建议做一道同类变式题。"
    elif not is_correct and p_new < 0.4:
        action = "remediate"
        message = "掌握度较低，建议先补讲再做降阶题。"
    else:
        action = "reinforce"
        message = "继续巩固，做一道迁移题确认理解。"

    return {
        "p_mastery": round(p_new, 4),
        "action": action,
        "message": message,
    }


# ── 间隔重复调度 ──────────────────────────────────────

REVIEW_THRESHOLD = 0.6   # 掌握度低于此值需要复习
URGENT_HOURS = 2         # 2小时内会降到阈值以下 → 紧急
SOON_HOURS = 12          # 12小时内 → 即将到期


def hours_until_threshold(p_mastery: float, params: BKTParams,
                          review_count: int, threshold: float = REVIEW_THRESHOLD) -> float:
    """
    计算从当前掌握度衰减到阈值以下需要多少小时。
    P(L_t) = P(L) * exp(-lambda * t)
    t = -ln(threshold / P(L)) / lambda
    """
    if p_mastery <= threshold:
        return 0.0
    effective_lambda = params.decay_rate / (1 + 0.3 * min(review_count, 20))
    if effective_lambda <= 0:
        return float('inf')
    import math
    t = -math.log(threshold / p_mastery) / effective_lambda
    return max(0.0, t)


def compute_review_schedule(topics: list[dict]) -> dict:
    """
    根据 BKT 数据计算间隔重复调度。

    Args:
        topics: 每个 topic 的 BKT 数据（含 p_mastery, params, review_count, last_reviewed_at）

    Returns:
        dict with due_topics (今日待复习) and upcoming_topics (即将到期)
    """
    from datetime import datetime, timezone

    due_topics = []
    upcoming_topics = []

    for topic_data in topics:
        p_mastery = topic_data.get("p_mastery", 0.3)
        params = BKTParams(
            p_init=topic_data.get("p_init", 0.3),
            p_transit=topic_data.get("p_transit", 0.1),
            p_guess=topic_data.get("p_guess", 0.25),
            p_slip=topic_data.get("p_slip", 0.1),
            decay_rate=topic_data.get("decay_rate", 0.05),
        )
        review_count = topic_data.get("review_count", 0)

        # 计算距上次复习的小时数
        hours_since = 0.0
        last_reviewed = topic_data.get("last_reviewed_at")
        if last_reviewed:
            try:
                last_dt = datetime.fromisoformat(str(last_reviewed))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                hours_since = max(0, (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass

        # 当前实际掌握度（含遗忘衰减）
        current_mastery = apply_forgetting(p_mastery, hours_since, params, review_count)

        # 计算距阈值还有多少小时
        hours_left = hours_until_threshold(current_mastery, params, review_count)

        entry = {
            "topic": topic_data.get("topic", ""),
            "p_mastery": round(current_mastery, 4),
            "hours_until_forget": round(hours_left, 1),
            "review_count": review_count,
            "attempts": topic_data.get("attempts", 0),
            "accuracy": topic_data.get("accuracy", 0),
        }

        if current_mastery < REVIEW_THRESHOLD:
            entry["urgency"] = "overdue"
            due_topics.append(entry)
        elif hours_left <= URGENT_HOURS:
            entry["urgency"] = "urgent"
            due_topics.append(entry)
        elif hours_left <= SOON_HOURS:
            entry["urgency"] = "soon"
            upcoming_topics.append(entry)

    # 按紧急度排序：overdue > urgent > soon，同级按掌握度升序
    urgency_order = {"overdue": 0, "urgent": 1, "soon": 2}
    due_topics.sort(key=lambda x: (urgency_order.get(x["urgency"], 9), x["p_mastery"]))
    upcoming_topics.sort(key=lambda x: x["p_mastery"])

    return {
        "due_topics": due_topics,
        "upcoming_topics": upcoming_topics,
        "total_due": len(due_topics),
    }
