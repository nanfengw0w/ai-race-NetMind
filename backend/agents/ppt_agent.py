"""Generate freely designed slides using the complete presentation control template."""
import random
import re
import time
from html.parser import HTMLParser
from pathlib import Path

from agents.design_styles import DESIGN_STYLES, learning_topic_title, style_instruction
from agents.generation_quality import CONTENT_ACCURACY, VISUAL_ACCURACY, HTML_RUNTIME, generation_thinking
from services.llm_client import LLM_MAX_OUTPUT_TOKENS, chat_with_diagnostics


SYSTEM_PROMPT = """根据用户的问题和参考资料制作可独立讲授的完整 PPT课件，自主安排内容、页数和视觉设计。先在内部规划讲解脉络与逐页构图，再一次输出完整课件。
用户指定页数或详略时以用户要求为准；未指定时按知识覆盖与阅读密度自然分页，把关键机制、具体例子和适用边界展开讲清，不把完整主题压缩成封面、概念、流程、总结几页。每页推进新信息，不靠空白过渡页、目录或重复总结凑页数。
以动画演示为主，用有含义的示意图、数据流、对照或推演解释具体问题，文字交代必要的定义、条件和结论；不要让装饰动效替代讲解，不要为了填模板补卡片。示例要具体且贯穿相关过程，不编造数据和来源。
不要使用大范围的圆角和相同的布局。
根据每页的内容关系重新组织构图、图文比例与视觉重心，避免全篇复用“上方标题、左文右图”或等宽卡片。统一字体与视觉语言，页面之间有疏密和强弱变化。
色彩以白底黑字和暗红点缀为基础，可使用深底反白、暖白、主题色面与局部纹理形成有意义的背景变化；变化贯穿正文，不只换封面和结尾，不要求每页白底，也不要全篇铺同一种网格。优先保证对比度和阅读舒适。
CSS 基础样式使用 :where(section.slide) 等低优先级选择器；单页主题的背景和文字颜色必须能覆盖基础样式，避免浅底配浅字。正文容器不得用固定高度加 overflow:hidden 裁掉内容；图示和装饰可裁切。
只输出可独立运行的完整 HTML，内含 CSS 和 JavaScript；每页使用 <section class="slide">，每页内容完整保留，以16:9演示画面组织布局，优先合理分页而非长页滚动或缩小字号，窄窗口仍可滚动查看。
在下面的完整模板中制作课件，保留翻页代码、页码和索引，把页面放入 #deck；标题只写学习主题。"""

PPT_STYLE_GUIDANCE = {
    '后现代主义设计审美': '使用不对称构图、字号对比、几何色面与局部叠压，让内容关系决定版式；装饰避让正文和图示，保持可读。',
    '瑞士国际主义': '使用清晰网格、严谨对齐、强字号层级和充足留白；跨页变化栏宽、主图尺度与色面位置，不把网格变成重复卡片。',
    '构成主义简洁设计审美': '使用大胆的几何结构、黑白与暗红色块、方向性线条和不对称张力；把构成用于解释主题，避免装饰压过内容。',
}
GENERATION_PROMPT_VERSION = 'ppt-formula-topology-v6'

NAVIGATION_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / 'assets' / 'ppt_navigation_template.html'


def build_system_prompt(design_style):
    """Send the actual controller source, rather than a description or placeholder."""
    template = NAVIGATION_TEMPLATE_PATH.read_text(encoding='utf-8')
    return f'{SYSTEM_PROMPT}\n{CONTENT_ACCURACY}\n{VISUAL_ACCURACY}\n{HTML_RUNTIME}\n{style_instruction(design_style)}\n{PPT_STYLE_GUIDANCE.get(design_style, "")}\n\n完整翻页模板：\n{template}'


class PptGenerationError(ValueError):
    def __init__(self, message, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response


class DeckShape(HTMLParser):
    """Check the document/page boundary without rewriting generated content."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.started = {}
        self.closed = {}
        self.title_parts = []
        self.in_title = False
        self.deck_version = None
        self.html_start_tag = None
        self.html_start_position = None
        self.sections = []
        self.slides = 0
        self.closed_slides = 0
        self.slides_outside_body = False
        self.in_body = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.started[tag] = self.started.get(tag, 0) + 1
        if tag == 'html':
            self.deck_version = attributes.get('data-netmind-deck')
            self.html_start_tag = self.get_starttag_text()
            self.html_start_position = self.getpos()
        elif tag == 'body':
            self.in_body = True
        elif tag == 'title':
            self.in_title = True
        elif tag == 'section':
            is_slide = 'slide' in (attributes.get('class') or '').split()
            self.sections.append(is_slide)
            if is_slide:
                self.slides += 1
                self.slides_outside_body |= not self.in_body

    def handle_endtag(self, tag):
        self.closed[tag] = self.closed.get(tag, 0) + 1
        if tag == 'body':
            self.in_body = False
        elif tag == 'title':
            self.in_title = False
        elif tag == 'section' and self.sections:
            self.closed_slides += int(self.sections.pop())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)


def _clean_html(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise PptGenerationError('模型未返回可用的 PPT课件 HTML 源码。', raw)
    clean = raw.strip().lstrip('\ufeff').lstrip()
    # Local source extraction removes only prose/fences outside the HTML file.
    # Never convert JSON, shorten, replace or re-render page content/design.
    if clean.startswith(('{', '[')):
        raise PptGenerationError('模型未返回完整 HTML 课件源码，请重试。', raw)
    outside_comments = re.sub(r'<!--[\s\S]*?-->', lambda match: ' ' * len(match.group()), clean)
    opening = re.search(r'<html\b', outside_comments, re.I)
    endings = list(re.finditer(r'</html\s*>', outside_comments, re.I))
    if not opening or not endings or endings[-1].start() <= opening.start():
        raise PptGenerationError('PPT课件 HTML 未完整结束，请重试。', raw)
    prefix = clean[:opening.start()]
    doctype = re.search(r'<!doctype\s+html\b[^>]*>', prefix, re.I)
    start = doctype.start() if doctype else opening.start()
    comments_only = re.sub(r'<!--[\s\S]*?-->|<!doctype\s+html\b[^>]*>', '', prefix, flags=re.I).strip()
    if not comments_only:
        start = 0
    end = endings[-1].end()
    if not re.sub(r'<!--[\s\S]*?-->', '', clean[end:]).strip():
        end = len(clean)
    return clean[start:end].strip()


def _inspect_document(html):
    shape = DeckShape()
    try:
        shape.feed(html)
        shape.close()
    except Exception:
        raise PptGenerationError('PPT课件 HTML 结构无法解析，请重试。') from None
    for tag in ('html', 'body'):
        if shape.started.get(tag) != 1 or shape.closed.get(tag) != 1:
            raise PptGenerationError(f'PPT课件缺少唯一完整的 {tag} 开始和结束标签。')
    if shape.deck_version not in (None, 'v2'):
        raise PptGenerationError('PPT课件分页版本不受支持。')
    if not shape.slides or shape.closed_slides != shape.slides or shape.slides_outside_body:
        raise PptGenerationError('PPT课件需要完整的 section.slide 页面。')
    return shape


def _validation_report(shape):
    return {
        'passed': True, 'scope': 'static', 'runtime_verified': False,
        'slide_count': shape.slides,
        'checks': ['唯一完整 HTML 和 body、分页标记及完整 section.slide'],
        'limitations': ['仅检查文档和分页结构，未执行脚本或验证学科事实；预览由浏览器沙箱隔离。'],
    }


def parse_ppt_response(raw, diagnostics=None):
    if (diagnostics or {}).get('finish_reason') == 'length':
        raise PptGenerationError('模型输出达到长度上限，PPT课件未完成；请缩小问题后重试。', raw)
    try:
        html = _clean_html(raw)
        shape = _inspect_document(html)
    except PptGenerationError as exc:
        if exc.raw_response is None:
            exc.raw_response = raw
        raise
    if shape.deck_version is None:
        # The marker belongs to our presentation runtime, not the model's design.
        # Inserting it changes only the HTML root tag; every page stays intact.
        opening = shape.html_start_tag
        marked = opening[:-1] + ' data-netmind-deck="v2">'
        line, column = shape.html_start_position
        offset = sum(len(part) + 1 for part in html.split('\n')[:line - 1]) + column
        html = html[:offset] + marked + html[offset + len(opening):]
    title = learning_topic_title(re.sub(r'\s+', ' ', ''.join(shape.title_parts)).strip()) or 'PPT课件'
    return title, html, _validation_report(shape)


def generate_ppt(profile, context, query):
    question = str(query or '').strip()
    references = str(context or '').strip()
    if references:
        question += '\n\n参考资料：\n' + references
    design_style = random.choice(DESIGN_STYLES)
    messages = [{'role': 'system', 'content': build_system_prompt(design_style)}, {'role': 'user', 'content': question}]
    started = time.perf_counter()
    try:
        raw, diagnostics = chat_with_diagnostics(
            messages, temperature=0.7, max_tokens=LLM_MAX_OUTPUT_TOKENS, thinking=generation_thinking(),
        )
    except Exception:
        raise PptGenerationError('PPT课件生成请求失败，请检查模型服务后重试。') from None
    title, html, validation = parse_ppt_response(raw, diagnostics)
    return {'type': 'ppt', 'title': title, 'content': html, 'metadata': {
        'related_topics': (profile or {}).get('薄弱知识点', []),
        'format': 'netmind-html-deck-v2', 'schema_version': 5, 'editable': False,
        'design_style': design_style,
        'generation_prompt_version': GENERATION_PROMPT_VERSION,
        'slide_count': validation['slide_count'], 'structure_validation': validation,
        'generation_diagnostics': {
            **(diagnostics or {}), 'llm': diagnostics or {},
            'html_chars': len(html), 'rendered_slides': validation['slide_count'],
            'timings_ms': {'total': round((time.perf_counter() - started) * 1000)},
        },
    }}


def review_ppt_resource(resource, action=None):
    try:
        _, _, validation = parse_ppt_response(resource.get('content'))
        return {'tool': 'ppt', 'passed': True, 'issues': [], 'decision': 'accept',
                'scope': 'static', 'runtime_verified': False, 'limitations': validation['limitations']}
    except PptGenerationError as exc:
        return {'tool': 'ppt', 'passed': False, 'issues': [str(exc)], 'decision': 'reject',
                'scope': 'static', 'runtime_verified': False}
