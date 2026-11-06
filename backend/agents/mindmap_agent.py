import json

from services.llm_client import chat

SYSTEM_PROMPT = """依据学习需求与资料，用简短节点呈现主题及其关系，按内容需要展开。
只返回 Mermaid mindmap 文本，以mindmap开头、root((主题))为根，用缩进表示层级。"""


def generate_mindmap(profile: dict, context: str, query: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"画像：{json.dumps(profile, ensure_ascii=False)}\n参考资料：{context}\n需求：{query}"},
    ]

    content = chat(messages, temperature=0.5)
    mermaid_code = content.strip()
    if mermaid_code.startswith("```"):
        mermaid_code = mermaid_code.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    return {
        "type": "mindmap",
        "title": f"思维导图 - {profile.get('当前课程', '当前课程')}",
        "content": mermaid_code,
        "metadata": {
            "format": "mermaid",
            "related_topics": profile.get("薄弱知识点", []),
        },
    }
