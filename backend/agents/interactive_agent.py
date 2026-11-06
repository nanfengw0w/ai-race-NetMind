"""One model call returns raw HTML; browser sandbox/CSP provides isolation."""
import random
import re
from html.parser import HTMLParser

from agents.design_styles import DESIGN_STYLES, learning_topic_title as _learning_topic_title, style_instruction
from agents.generation_quality import CONTENT_ACCURACY, VISUAL_ACCURACY, INTERACTION_ACCURACY, HTML_RUNTIME, generation_thinking
from services.llm_client import LLM_MAX_OUTPUT_TOKENS, chat_with_diagnostics

SYSTEM_PROMPT = """根据用户的问题制作以动画交互为主的教学网页，动画演示是主体，让用户通过操作交互页面观察过程和变化，文字仅作简短说明。
生成可独立运行的完整 HTML 文件，包含 CSS 和 JavaScript，让动画与用户操作联动。
只输出 HTML 源码。网页色彩采用白底黑字和暗红点缀。
不要使用大范围的圆角和相同的布局。"""

GENERATION_PROMPT_VERSION = 'interactive-boundary-topology-v5'


class InteractiveGenerationError(ValueError):
    def __init__(self, message, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response


class DocumentShape(HTMLParser):
    """Inspect tag completeness only; never execute or judge generated scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.started = {}
        self.closed = {}
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        self.started[tag] = self.started.get(tag, 0) + 1
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag):
        self.closed[tag] = self.closed.get(tag, 0) + 1
        if tag == 'title':
            self.in_title = False

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)


def _clean_html(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise InteractiveGenerationError('模型未返回可用的 HTML 网页源码。', raw)
    clean = raw.strip().lstrip('\ufeff').lstrip()
    # This is local source extraction, not a second model call or JSON protocol.
    if clean.startswith(('{', '[')):
        raise InteractiveGenerationError('模型未返回 HTML 源码，请重试。', raw)
    opening_fence = re.match(r'^```(?:html)?[ \t]*\r?\n', clean, re.I)
    if opening_fence and clean.rstrip().endswith('```'):
        clean = clean[opening_fence.end():].rstrip()[:-3].strip()
    opening = re.search(r'<html\b[^>]*>', clean, re.I)
    endings = list(re.finditer(r'</html\s*>', clean, re.I))
    if not opening or not endings or endings[-1].start() <= opening.start():
        raise InteractiveGenerationError('模型未返回完整 HTML 文档；需要 HTML 开始和结束标签。', raw)
    # Keep an optional doctype (and legal comments around it), while dropping prose
    # or Markdown that the browser must not display before/after the document.
    prefix = clean[:opening.start()]
    doctype = re.search(r'<!doctype\s+html\b[^>]*>', prefix, re.I)
    start = doctype.start() if doctype else opening.start()
    comments_only = re.sub(r'<!--[\s\S]*?-->|<!doctype\s+html\b[^>]*>', '', prefix, flags=re.I).strip()
    if not comments_only:
        start = 0
    return clean[start:endings[-1].end()].strip()


def _inspect_document(html):
    document = DocumentShape()
    try:
        document.feed(html)
        document.close()
    except Exception:
        raise InteractiveGenerationError('互动教学 HTML 结构无法解析，请重试。') from None
    for tag in ('html', 'body'):
        if document.started.get(tag) != 1 or document.closed.get(tag) != 1:
            raise InteractiveGenerationError(f'互动教学缺少完整的 {tag} 开始或结束标签。')
    return document


def validate_interactive_html(html):
    try:
        _inspect_document(_clean_html(html))
    except InteractiveGenerationError as exc:
        if exc.raw_response is None:
            exc.raw_response = html
        raise
    return {
        'passed': True, 'scope': 'static', 'runtime_verified': False,
        'checks': ['非空完整 HTML 文档及 body 标签'],
        'limitations': ['仅检查文档完整性，未执行脚本或验证交互、计算与学科事实；预览由浏览器沙箱和 CSP 隔离。'],
    }


def parse_interactive_response(raw, diagnostics=None):
    """The model protocol is raw HTML, separate from our internal resource dict."""
    if (diagnostics or {}).get('finish_reason') == 'length':
        raise InteractiveGenerationError('模型输出达到长度上限，网页可能未完成；请缩小问题后重试。', raw)
    try:
        html = _clean_html(raw)
        document = _inspect_document(html)
    except InteractiveGenerationError as exc:
        if exc.raw_response is None:
            exc.raw_response = raw
        raise
    title = _learning_topic_title(re.sub(r'\s+', ' ', ''.join(document.title_parts)).strip())[:120] or '互动教学'
    return title, html, validate_interactive_html(html)


def generate_interactive(profile, context, query):
    # The legacy profile argument remains for caller compatibility, but the model
    # receives only the question and supplied reference text, without an envelope.
    question = str(query or '').strip()
    references = str(context or '').strip()
    if references:
        question += '\n\n参考资料：\n' + references
    design_style = random.choice(DESIGN_STYLES)
    messages = [{'role': 'system', 'content': f'{SYSTEM_PROMPT}\n{CONTENT_ACCURACY}\n{VISUAL_ACCURACY}\n{INTERACTION_ACCURACY}\n{HTML_RUNTIME}\n{style_instruction(design_style)}'}, {'role': 'user', 'content': question}]
    try:
        raw, diagnostics = chat_with_diagnostics(messages, temperature=0.4, max_tokens=LLM_MAX_OUTPUT_TOKENS, thinking=generation_thinking())
    except Exception:
        raise InteractiveGenerationError('互动教学生成请求失败，请检查模型服务后重试。') from None
    title, html, validation = parse_interactive_response(raw, diagnostics)
    return {'type': 'interactive', 'title': title, 'content': html, 'metadata': {
        'format': 'html', 'design_style': design_style, 'generation_prompt_version': GENERATION_PROMPT_VERSION,
        'structure_validation': validation, 'generation_diagnostics': diagnostics,
    }}


def review_interactive_resource(resource, action=None):
    try:
        validation = validate_interactive_html(resource.get('content'))
        return {'tool': 'interactive', 'passed': True, 'issues': [], 'decision': 'accept',
                'scope': 'static', 'runtime_verified': False, 'limitations': validation['limitations']}
    except InteractiveGenerationError as exc:
        return {'tool': 'interactive', 'passed': False, 'issues': [str(exc)], 'decision': 'reject',
                'scope': 'static', 'runtime_verified': False}
