import json

from services.llm_client import chat


REPORT_SYSTEM_PROMPT = """根据本次需求、已有答题记录和学习者自述，用 Markdown 回顾进展，说明证据支持什么、还不能判断什么，并提出有依据的后续探索建议。
缺少答题证据时明确说明，不编造能力分、薄弱点或预计成效。"""


def generate_assessment_report(profile: dict, quiz_records: list[dict], query: str = "") -> dict:
    """Generate a Markdown assessment resource for the normal resource workflow."""
    records_desc = json.dumps(quiz_records[-10:], ensure_ascii=False)
    has_records = len(quiz_records) > 0

    messages = [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"画像：{json.dumps(profile, ensure_ascii=False)}\n最近答题：{records_desc if has_records else '暂无答题记录'}\n需求：{query or '回顾当前学习进展'}",
        },
    ]

    content = chat(messages, temperature=0.35)
    return {
        "type": "assessment",
        "title": "学习评估报告 - 当前阶段",
        "content": content,
        "metadata": {
            "record_count": len(quiz_records),
            "has_quiz_records": has_records,
            "related_topics": profile.get("薄弱知识点", []),
        },
    }
