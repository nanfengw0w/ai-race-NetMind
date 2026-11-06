import json
from services.llm_client import LLM_MAX_OUTPUT_TOKENS, chat
from agents.topic_utils import merge_topics, normalize_topic, topic_from_query

SYSTEM_PROMPT = """按学习者的需求、资料和已说明的基础出题，数量、题型与难度随问题安排，不把提问当作薄弱证据。
只返回 JSON 数组，每题包含：{"type":"choice/judge/fill/short/calculation/code","question":"题干","options":[],"answer":"答案","accepted_answers":[],"answer_keywords":[],"explanation":"解析","topic":"知识点"}。
type选用其中一种；选择题options带字母标记，answer为对应字母；判断题options为["A. 正确","B. 错误"]，answer为A或B；其他题options为空。同义答案填入accepted_answers，简答评分要点填入answer_keywords；topic优先复用已有概念。"""


def generate_quiz(profile: dict, context: str, query: str, existing_topics: list[str] | None = None) -> dict:
    profile_desc = json.dumps(profile, ensure_ascii=False)

    topics_hint = ""
    if existing_topics:
        topics_hint = f"\n已有知识点：{', '.join(existing_topics)}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"画像：{profile_desc}\n参考资料：{context}\n需求：{query}{topics_hint}"},
    ]

    result = chat(messages, temperature=0.5, max_tokens=LLM_MAX_OUTPUT_TOKENS)
    json_str = result.strip()
    candidates = [json_str]
    if json_str.startswith("```"):
        candidates.append(json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip())
    array_start = json_str.find("[")
    array_end = json_str.rfind("]")
    if array_start >= 0 and array_end > array_start:
        candidates.append(json_str[array_start:array_end + 1])

    questions = []
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            questions = data
            break
        if isinstance(data, dict) and isinstance(data.get("questions"), list):
            questions = data["questions"]
            break

    # 归并细粒度 topic 到大类
    for q in questions:
        if isinstance(q, dict) and q.get("topic"):
            q["topic"] = normalize_topic(q["topic"])

    topics = list(dict.fromkeys(
        q.get("topic", "") for q in questions if q.get("topic")
    ))
    topic_hint = "、".join(topics[:3]) if topics else topic_from_query(query, profile)
    course = profile.get("当前课程", "当前课程")
    title = f"练习题 - {topic_hint}" if topic_hint else f"练习题 - {course}"

    return {
        "type": "quiz",
        "title": title,
        "content": json.dumps(questions, ensure_ascii=False, indent=2),
        "metadata": {
            "questions": questions,
            "related_topics": merge_topics(topics, profile.get("薄弱知识点", [])),
        },
    }
