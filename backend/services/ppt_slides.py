"""Locate and replace a slide in original HTML without serializing the document."""
import re
from html.parser import HTMLParser


VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
RAW = {'script', 'style', 'textarea', 'title', 'xmp', 'iframe', 'noembed', 'noframes'}


class SourceTree(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.lines = [0] + [match.end() for match in re.finditer('\n', source)]
        self.nodes = []
        self.stack = []
        self.feed(source)
        self.close()
        if self.stack:
            raise ValueError('课件含未闭合标签，无法精确定位页面。')

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == 'plaintext' or len(dict(attrs)) != len(attrs):
            raise ValueError('课件含不能精确定位的标签或重复属性。')
        start = self.position()
        node = {'tag': tag, 'attrs': dict(attrs), 'start': start,
                'open_end': start + len(self.get_starttag_text()),
                'parent': self.stack[-1] if self.stack else None, 'text': []}
        self.nodes.append(node)
        if tag in VOID:
            node['end'] = node['open_end']
        else:
            self.stack.append(node)
            if tag in RAW:
                self.set_cdata_mode(tag)

    def handle_startendtag(self, tag, attrs):
        # HTML ignores a self-closing slash on ordinary non-void elements.
        foreign = any(node['tag'] in {'svg', 'math'} for node in self.stack)
        if tag not in VOID and tag not in {'svg', 'math'} and not foreign:
            raise ValueError('HTML 非空元素使用了自闭合写法，无法安全修改单页。')
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            node = self.stack.pop()
            node['end'] = node['open_end']

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]['tag'] != tag:
            raise ValueError('课件标签边界不匹配，无法安全修改单页。')
        node = self.stack.pop()
        start = self.position()
        closing = re.match(r'</\s*' + re.escape(tag) + r'\s*>', self.source[start:], re.I)
        if not closing:
            raise ValueError('课件结束标签无法精确定位。')
        node['end'] = start + closing.end()

    def handle_data(self, data):
        for node in self.stack:
            if node['tag'] in {'h1', 'h2', 'h3'}:
                node['text'].append(data)


def _is_slide(node):
    return node['tag'] == 'section' and 'slide' in (node['attrs'].get('class') or '').split()


def _ancestor(node, parent):
    while node is not None:
        if node is parent:
            return True
        node = node['parent']
    return False


def _ids(tree):
    ids = [node['attrs']['id'] for node in tree.nodes if node['attrs'].get('id')]
    if len(ids) != len(set(ids)):
        raise ValueError('课件存在重复元素 ID，无法安全修改单页。')
    return set(ids)


def _slides(tree):
    bodies = [node for node in tree.nodes if node['tag'] == 'body']
    roots = [node for node in tree.nodes if node['tag'] == 'html']
    slides = [node for node in tree.nodes if _is_slide(node)]
    decks = [node for node in tree.nodes if node['attrs'].get('id') == 'deck']
    if len(bodies) != 1 or len(roots) != 1 or not slides or not _ancestor(bodies[0], roots[0]):
        raise ValueError('需要完整 HTML 课件和 section.slide 页面。')
    parent = decks[0] if len(decks) == 1 else slides[0]['parent']
    if len(decks) > 1 or parent is None or any(node['parent'] is not parent for node in slides):
        raise ValueError('页面必须位于同一容器下，不能嵌套幻灯片。')
    if not _ancestor(parent, bodies[0]):
        raise ValueError('页面必须位于课件正文内。')
    if any(_ancestor(parent, node) for node in tree.nodes if node['tag'] in {'template', 'svg', 'math'}):
        raise ValueError('模板或图形中的标签不能作为课件页面。')
    _ids(tree)
    return slides


def list_slides(content):
    tree = SourceTree(content)
    result = []
    for index, node in enumerate(_slides(tree), 1):
        headings = [child for child in tree.nodes if child['tag'] in {'h1', 'h2', 'h3'} and _ancestor(child, node)]
        title = re.sub(r'\s+', ' ', ''.join(headings[0]['text'])).strip() if headings else ''
        result.append({'number': index, 'title': title or f'第 {index} 页', 'selection': {
            'start': node['start'], 'end': node['end'],
            'text': content[node['start']:node['end']], 'slide_number': index,
        }})
    return result


def _protected(tree):
    blocks = [tree.source[node['start']:node['end']] for node in tree.nodes if node['tag'] in {'script', 'style'}]
    events = [(node['tag'], key, value) for node in tree.nodes for key, value in node['attrs'].items()
              if key.startswith('on') or key in {'srcdoc'} or (isinstance(value, str) and re.match(r'\s*javascript:', value, re.I))]
    return blocks, events


def replace_slide(content, selection, raw):
    replacement = str(raw).strip()
    fence = re.fullmatch(r'```(?:html)?\s*([\s\S]*?)\s*```', replacement, re.I)
    if fence:
        replacement = fence[1].strip()
    tree = SourceTree(replacement)
    top = [node for node in tree.nodes if node['parent'] is None]
    if len(top) != 1 or not _is_slide(top[0]) or top[0]['start'] != 0 or top[0]['end'] != len(replacement):
        raise ValueError('单页修改必须只返回一个完整 section.slide，不能返回整份课件或多页。')
    root = top[0]
    original = SourceTree(selection['text'])
    old_root = original.nodes[0]
    for attr in ('id', 'class'):
        if root['attrs'].get(attr) != old_root['attrs'].get(attr):
            raise ValueError('修改结果改变了页面标识或基础布局，旧版已保留，请只描述本页内容的修改。')
    if _protected(tree) != _protected(original):
        raise ValueError('单页修改不能新增或改写脚本、全局样式和事件；可使用现有样式或行内样式。')
    if any(node['tag'] in {'html', 'head', 'body', 'base', 'link', 'meta', 'iframe', 'object', 'embed'} for node in tree.nodes):
        raise ValueError('单页结果包含会改变文档环境的标签。')
    if not _ids(original).issubset(_ids(tree)):
        raise ValueError('单页修改不能删除原有元素 ID，以免破坏课件交互。')
    result = content[:selection['start']] + replacement + content[selection['end']:]
    before, after = list_slides(content), list_slides(result)
    if len(before) != len(after):
        raise ValueError('单页修改不能新增、删除或嵌套页面。')
    for old, new in zip(before, after):
        if old['number'] != selection['slide_number'] and old['selection']['text'] != new['selection']['text']:
            raise ValueError('其他页面原文发生变化，已取消保存。')
    return result


def restore_slide(content, historical, number):
    """Copy a saved page exactly into the current deck, without model output."""
    if type(number) is not int or number < 1:
        raise ValueError('恢复页码必须是正整数。')
    current_tree, source_tree = SourceTree(content), SourceTree(historical)
    current, source = _slides(current_tree), _slides(source_tree)
    if number > len(current) or number > len(source):
        raise ValueError(f'当前课件共 {len(current)} 页，历史版本共 {len(source)} 页，无法恢复第 {number} 页。')
    current_ids = [node['attrs'].get('id') for node in current]
    source_ids = [node['attrs'].get('id') for node in source]
    # Page numbers are the user's explicit source/destination coordinates.
    # Existing generated decks may omit page IDs; do not invent identifiers.
    # If IDs are present, also reject known reorders or identity changes.
    if current_ids != source_ids:
        raise ValueError('两版课件的页数、页面标识或顺序不同，无法可靠匹配同一页；请使用整份恢复。')

    def shell(text, pages):
        cursor, parts = 0, []
        for page in pages:
            parts.append(text[cursor:page['start']])
            cursor = page['end']
        return parts + [text[cursor:]]

    if shell(content, current) != shell(historical, source):
        raise ValueError('历史版本的公共样式或课件框架已变化，无法单页恢复并保持当前交互；请使用整份恢复。')
    target, saved = current[number - 1], source[number - 1]
    old_text = content[target['start']:target['end']]
    saved_text = historical[saved['start']:saved['end']]
    if _protected(SourceTree(old_text)) != _protected(SourceTree(saved_text)):
        raise ValueError('两版页面内的脚本、样式或事件不同，无法安全混合恢复；请使用整份恢复。')
    result = content[:target['start']] + saved_text + content[target['end']:]
    # Reparse the combined source to reject ID collisions with current pages.
    merged = _slides(SourceTree(result))
    if len(merged) != len(current):
        raise ValueError('恢复结果改变了课件页数，本次未保存。')
    return result


NUMBER = r'[0-9零〇一二两三四五六七八九十百千]+'
PAGE = re.compile(r'(?:第\s*(' + NUMBER + r')\s*(?:页|张)(?:幻灯片)?|\b(?:slide|page)\s*#?\s*(\d+)\b)', re.I)
WHOLE = re.compile(r'整份|整[份个套本].{0,4}(?:课件|PPT|ppt)|全部页面|整篇|整套|全文|\b(?:whole|entire)\s+(?:deck|presentation)|\ball\s+(?:slides|pages)', re.I)


def _number(value):
    if value.isdecimal():
        return int(value)
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    units = {'十': 10, '百': 100, '千': 1000}
    if not any(char in units for char in value):
        return int(''.join(str(digits[char]) for char in value))
    total = digit = 0
    for char in value:
        if char in units:
            total += (digit or 1) * units[char]
            digit = 0
        else:
            digit = digits[char]
    return total + digit


def requested_slide(instruction, selected_number=None):
    """Conservative explicit page scope; never silently fall back to a whole edit."""
    matches = list(PAGE.finditer(instruction))
    numbers = {_number(next(value for value in match.groups() if value is not None)) for match in matches}
    ranges = re.search(r'(?:第\s*)?' + NUMBER + r'\s*(?:[、,，/\-—~～至到和及与]|或)\s*(?:第\s*)?' + NUMBER + r'\s*[页张]', instruction)
    plural = re.search(r'多页|几页|每[页张]|各[页张]|所有[页张]|全部[页张]|[前后头末]'+ NUMBER + r'[页张]|其[他余]页.*(?:也|都).*改', instruction)
    ranges = ranges or re.search(r'\b(?:slides?|pages?)\s*\d+\s*(?:[-–—~,]|to|through|and)\s*\d+', instruction, re.I)
    whole = WHOLE.search(instruction)
    if len(numbers) > 1 or ranges or (plural and (numbers or selected_number)) or (whole and (numbers or selected_number)):
        raise ValueError('单页修改一次只处理一页，请明确选择一页，并只描述这一页的修改。')
    number = next(iter(numbers), None)
    if number is not None and selected_number is not None and number != selected_number:
        raise ValueError('请求页码与当前引用页面不同，请清除页面引用或重新选择。')
    if number is None and selected_number is None and not whole and re.search(r'[页张]|\b(?:slide|page)s?\b', instruction, re.I):
        raise ValueError('请明确指定要修改的页码，例如“只修改第三页”，或先选择页面。')
    return number if number is not None else selected_number
