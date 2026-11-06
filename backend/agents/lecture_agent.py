import json

from services.llm_client import LLM_MAX_OUTPUT_TOKENS, chat_with_diagnostics
from services.explanation_bundle import (
    ExplanationValidationError,
    parse_lecture_response,
    recover_readable_markdown,
    visual_validation,
)
from agents.topic_utils import merge_topics, lecture_title_topic
from agents.generation_quality import CONTENT_ACCURACY, generation_thinking

BUNDLE_PROMPT = """图解字段：{"schema_version":1,"title":"标题","summary":"说明","nodes":[{"id":"n1","label":"实际对象","detail":"含义"}],"edges":[],"steps":[{"title":"阶段","caption":"当前变化及原因","active_nodes":["n1"],"active_edges":[],"duration":6}]}；边为{"from":"节点id","to":"节点id","label":"具体关系"}，仅输出这些数据字段。
图解最多12个节点、24条边、8个步骤；id唯一，引用已有节点，active_edges为边的0起下标，每步高亮相关节点，duration为2–12秒。"""

SYSTEM_PROMPT = """根据学习者的问题、资料和明确偏好讲清原理，正文与图解使用一致的事实和例子，不虚构来源或学习证据。
只返回 JSON：{"markdown":"Markdown正文","bundle":图解对象或null}；适合解释关系或过程时提供图解，否则用null。
正文数学表达式用 $...$ 或 $$...$$ 包裹的 LaTeX，JSON 中正确转义反斜杠；不要把含上下标或分式的公式作为普通文字。图解节点只使用简短可直接显示的文字和 Unicode 符号，不放 LaTeX 源码。
""" + CONTENT_ACCURACY + '\n' + BUNDLE_PROMPT

GENERATION_PROMPT_VERSION = 'lecture-grounded-math-v4'


def generate_lecture(profile: dict, context: str, query: str) -> dict:
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': json.dumps({'profile': profile, 'reference_context': context, 'question': query}, ensure_ascii=False)},
    ]
    raw, diagnostics = chat_with_diagnostics(messages, temperature=0.45, max_tokens=LLM_MAX_OUTPUT_TOKENS, thinking=generation_thinking())
    content = recover_readable_markdown(raw)
    bundle = None
    error = None
    repair_attempted = False
    try:
        if diagnostics.get('finish_reason') == 'length':
            raise ExplanationValidationError('讲解方案达到模型长度上限，结构可能不完整')
        content, bundle = parse_lecture_response(raw)
    except ExplanationValidationError as first_error:
        repair_attempted = True
        repair_request = messages + [
            {'role': 'assistant', 'content': raw},
            {'role': 'user', 'content': '修正以下协议错误并返回完整 JSON，保留正文：' + str(first_error)[:1200]},
        ]
        try:
            repaired_raw, repaired_diagnostics = chat_with_diagnostics(repair_request, temperature=0.2, max_tokens=LLM_MAX_OUTPUT_TOKENS, thinking=generation_thinking())
            repaired_content = recover_readable_markdown(repaired_raw)
            if repaired_content:
                content = repaired_content
            diagnostics = {**repaired_diagnostics, 'initial_request': diagnostics}
            if repaired_diagnostics.get('finish_reason') == 'length':
                raise ExplanationValidationError('修复后的方案仍达到模型长度上限')
            content, bundle = parse_lecture_response(repaired_raw)
        except ExplanationValidationError as exc:
            error = '图解方案经一次修复仍未通过检查：' + str(exc)[:900]
        except Exception:
            error = '图解方案未通过检查，且一次修复请求失败；已保留可读文字，请稍后重试。'
        if error and not content:
            raise ExplanationValidationError('讲解未返回可读正文，图解修复也未完成，请重试。') from None

    topic = lecture_title_topic(content, bundle if not error else None, query, profile)
    course = profile.get("当前课程", "当前课程")
    title = f"课程讲解 - {topic}" if topic else f"课程讲解 - {course}"
    metadata = {
        'generation_prompt_version': GENERATION_PROMPT_VERSION,
        'related_topics': merge_topics(topic, profile.get('薄弱知识点', [])),
        'difficulty': profile.get('知识基础', 'medium'),
        'explanation_bundle': None if error else bundle,
        'visualization_validation': visual_validation(bundle, error),
        'visualization_repair_attempted': repair_attempted,
        'generation_diagnostics': diagnostics,
    }
    if error:
        metadata['visualization_error'] = error
    return {
        "type": "lecture",
        "title": title,
        "content": content,
        "metadata": metadata,
    }
