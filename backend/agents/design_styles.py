"""The three visual directions available to generated teaching resources."""
import re

DESIGN_STYLES = ('后现代主义设计审美', '瑞士国际主义', '构成主义简洁设计审美')


def style_instruction(design_style):
    return f'本次设计风格：{design_style}。风格只用于视觉设计，标题只写学习主题。'


def learning_topic_title(title):
    """Strip attached style labels from resource titles, preserving subject names."""
    # The retired label is recognized for old titles but never selected again.
    title_labels = (*DESIGN_STYLES, '简洁美观')
    style = '(?:' + '|'.join(re.escape(name) for name in title_labels) + ')'
    bracketed = rf'(?:\(\s*{style}\s*\)|（\s*{style}\s*）|\[\s*{style}\s*\]|【\s*{style}\s*】)'
    separator = r'[·•|/—–:：-]+'
    prefix = rf'^(?:{bracketed}\s*(?:{separator}\s*)?|{style}\s*{separator}\s*)(?P<topic>.+)$'
    suffix = rf'^(?P<topic>.+?)(?:\s*{separator}\s*{style}|\s*(?:{separator}\s*)?{bracketed})$'
    while True:
        match = re.match(prefix, title) or re.match(suffix, title)
        topic = match.group('topic').strip() if match else ''
        if not topic or topic == title:
            return title
        title = topic
