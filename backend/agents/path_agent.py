import json
from services.llm_client import chat

SYSTEM_PROMPT = """根据学习者说明的目标、背景和偏好，给出可以自主选择的学习顺序；不预设薄弱点，时长仅作估计，未知时留空。
只返回 JSON：{"total_duration":"估计总时长","phases":[{"phase":1,"title":"阶段","duration":"估计时长","tasks":[{"task":"探索内容","resource_type":"lecture/quiz/lab/mindmap","status":"pending"}]}]}，resource_type选用其中一种。"""


def generate_learning_path(profile: dict) -> dict:
    profile_desc = json.dumps(profile, ensure_ascii=False)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": profile_desc},
    ]

    result = chat(messages, temperature=0.5)
    try:
        json_str = result.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        path = json.loads(json_str)
    except json.JSONDecodeError:
        path = {"total_duration": "未知", "phases": [], "raw": result}

    return {
        "type": "learning_path",
        "title": f"学习路径 - {profile.get('当前课程', '')}",
        "content": json.dumps(path, ensure_ascii=False, indent=2),
        "metadata": path,
    }
