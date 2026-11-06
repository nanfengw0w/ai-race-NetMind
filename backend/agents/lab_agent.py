import json

from services.llm_client import chat
from agents.topic_utils import merge_topics, topic_from_query

SYSTEM_PROMPT = """依据学习需求、资料和偏好，给出能动手探索的实操说明，交代所需工具、具体操作与可观察的结果。
使用 Markdown，内容按实际问题组织，区分预期现象与已经获得的证据。"""


def generate_lab(profile: dict, context: str, query: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"画像：{json.dumps(profile, ensure_ascii=False)}\n参考资料：{context}\n需求：{query}"},
    ]

    content = chat(messages, temperature=0.7)
    topic = topic_from_query(query, profile)
    course = profile.get("当前课程", "")
    title = f"实操案例 - {topic}" if topic else f"实操案例 - {course}"
    return {
        "type": "lab",
        "title": title,
        "content": content,
        "metadata": {
            "related_topics": merge_topics(topic, profile.get("薄弱知识点", [])),
        },
    }
