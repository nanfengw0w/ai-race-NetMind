import json
from services.llm_client import chat

SYSTEM_PROMPT = """从学习者在对话中的明确自述提取背景、目标和偏好，不从提问推断薄弱、掌握或能力；未说明的信息保持未知。
只返回 JSON：{"专业方向":"未知","当前课程":"未知","知识基础":"unknown","薄弱知识点":[],"学习目标":"未知","认知偏好":"未知","学习节奏":"未知","易错点类型":[],"已有前置知识":[],"学习动机":"未知"}，用实际自述替换已知字段。"""


def extract_profile(message: str, history: list[dict] = None) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        for h in history[-6:]:
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    result = chat(messages, temperature=0.3)
    try:
        json_str = result.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {
            "专业方向": "未知",
            "当前课程": "未知",
            "知识基础": "medium",
            "薄弱知识点": [],
            "学习目标": "未明确",
            "认知偏好": "未明确",
            "学习节奏": "normal",
            "易错点类型": [],
            "已有前置知识": [],
            "学习动机": "未明确",
        }
