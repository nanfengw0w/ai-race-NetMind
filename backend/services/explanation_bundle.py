"""Validate data-only lecture visuals without changing the legacy resource shape."""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ExplanationValidationError(ValueError):
    pass


class BundleModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class ExplanationNode(BundleModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=1800)


class ExplanationEdge(BundleModel):
    source: str = Field(alias='from', min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)


class ExplanationStep(BundleModel):
    title: str = Field(min_length=1, max_length=120)
    caption: str = Field(min_length=1, max_length=1800)
    active_nodes: list[str] = Field(min_length=1, max_length=12)
    active_edges: list[int] = Field(default_factory=list, max_length=24)
    duration: float = Field(ge=2, le=12)


class ExplanationBundle(BundleModel):
    schema_version: Literal[1]
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=800)
    nodes: list[ExplanationNode] = Field(min_length=1, max_length=12)
    edges: list[ExplanationEdge] = Field(default_factory=list, max_length=24)
    steps: list[ExplanationStep] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def validate_references(self):
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError('图示节点 ID 必须唯一')
        known = set(ids)
        if any(edge.source not in known or edge.to not in known for edge in self.edges):
            raise ValueError('图示边必须指向已有节点')
        for step in self.steps:
            if any(node not in known for node in step.active_nodes):
                raise ValueError('步骤高亮节点必须存在')
            if any(index < 0 or index >= len(self.edges) for index in step.active_edges):
                raise ValueError('步骤高亮边下标超出范围')
            if len(step.active_nodes) != len(set(step.active_nodes)) or len(step.active_edges) != len(set(step.active_edges)):
                raise ValueError('同一步骤不得重复高亮同一节点或边')
        generic = {'概念', '核心概念', '原理', '应用', '总结', '开始', '结束', '节点', '步骤', '输入', '输出'}
        if all(re.sub(r'[\d\s一二三四五六七八九十]+', '', n.label) in generic for n in self.nodes):
            raise ValueError('图示不能全部使用空泛节点标签，请命名真实对象、变量或过程状态')
        return self


def validate_bundle(value):
    if value is None:
        return None
    try:
        if not isinstance(value, dict) or type(value.get('schema_version')) is not int:
            raise ExplanationValidationError('图解需要 schema_version=1 的结构化对象')
        validated = ExplanationBundle.model_validate(value)
        return validated.model_dump(by_alias=True)
    except ValidationError as exc:
        errors = ['.'.join(str(item) for item in e['loc']) + ': ' + e['msg'] for e in exc.errors(include_input=False)[:6]]
        raise ExplanationValidationError('；'.join(errors)) from None


def _json_text(raw):
    clean = str(raw or '').strip()
    if clean.startswith('```') and clean.endswith('```'):
        clean = re.sub(r'^```(?:json)?\s*', '', clean, flags=re.I)
        clean = clean[:-3].strip()
    return clean


def parse_lecture_response(raw):
    try:
        value = json.loads(_json_text(raw))
    except (ValueError, TypeError):
        raise ExplanationValidationError('模型未返回完整 JSON 讲解方案') from None
    if not isinstance(value, dict) or not isinstance(value.get('markdown'), str):
        raise ExplanationValidationError('讲解方案需要 markdown 正文和 bundle 字段')
    content = value['markdown'].strip()
    if not content or len(content) > 50000:
        raise ExplanationValidationError('讲解正文必须是非空且不超过 50000 字符的 Markdown')
    if 'bundle' not in value:
        raise ExplanationValidationError('讲解方案缺少 bundle；无可视化价值时应明确返回 null')
    return content, validate_bundle(value['bundle'])


def recover_readable_markdown(raw):
    """Retain received text, never display a malformed JSON document as prose."""
    clean = _json_text(raw)
    try:
        value = json.loads(clean)
        if isinstance(value, dict) and isinstance(value.get('markdown'), str):
            return value['markdown'].strip()[:50000]
    except (ValueError, TypeError):
        pass
    match = re.search(r'"markdown"\s*:\s*', clean)
    if match:
        try:
            content, _ = json.JSONDecoder().raw_decode(clean[match.end():])
            if isinstance(content, str):
                return content.strip()[:50000]
        except ValueError:
            pass
    if clean and not clean.startswith(('{', '[', '```json')):
        return clean[:50000]
    return ''


def visual_validation(bundle, error=None):
    if error:
        return {'passed': False, 'status': 'text_only_error', 'checks': [], 'limitations': ['图解未通过检查；仅保留已收到的可读正文。']}
    if bundle is None:
        return {'passed': None, 'status': 'not_applicable', 'checks': [], 'limitations': ['本次没有生成适用的图解与过程演示。']}
    return {'passed': True, 'status': 'valid', 'checks': ['字段类型与数量边界', '节点与边引用', '步骤时长与文本长度'],
            'limitations': ['仅检查结构有效性，学科事实与讲解质量仍需人工核验。']}


def review_lecture_resource(resource, action):
    """Schema-aware review, without the legacy word-count targets."""
    metadata = resource.get('metadata') or {}
    error = metadata.get('visualization_error')
    if error:
        return {'tool': 'lecture', 'passed': False, 'issues': [str(error)], 'decision': 'keep_text', 'visualization_passed': False}
    try:
        bundle = validate_bundle(metadata.get('explanation_bundle'))
    except ExplanationValidationError as exc:
        return {'tool': 'lecture', 'passed': False, 'issues': [str(exc)], 'decision': 'keep_text', 'visualization_passed': False}
    readable = bool(str(resource.get('content') or '').strip())
    return {'tool': 'lecture', 'passed': readable, 'issues': [] if readable else ['讲解正文为空'],
            'decision': 'accept' if readable else 'repair_once', 'visualization_passed': True if bundle else None}
