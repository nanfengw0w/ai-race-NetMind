import re


KNOWN_TOPICS = [
    (r"tcp\s*拥塞控制|拥塞控制", "TCP拥塞控制"),
    (r"tcp\s*三次握手|三次握手", "TCP三次握手"),
    (r"tcp\s*四次挥手|四次挥手", "TCP四次挥手"),
    (r"arp\s*协议|地址解析协议|arp", "ARP协议"),
    (r"dns|域名系统|域名解析", "DNS"),
    (r"https", "HTTPS"),
    (r"http", "HTTP"),
    (r"ipv6", "IPv6"),
    (r"ipv4", "IPv4"),
    (r"dhcp", "DHCP"),
    (r"nat|网络地址转换", "NAT"),
    (r"mac\s*地址|物理地址", "MAC地址"),
    (r"ip\s*地址", "IP地址"),
    (r"ip\s*协议", "IP协议"),
    (r"子网划分|子网掩码", "子网划分"),
    (r"路由协议|路由表|路由器", "路由协议"),
    (r"wireshark|抓包", "Wireshark"),
    (r"osi\s*七层|osi模型|七层模型", "OSI七层模型"),
]

PAIR_TOPIC_RULES = [
    (("IPv4", "IPv6"), r"区别|差异|对比|比较|异同|不同|地址格式|过渡|和|与|/", "IPv4/IPv6对比"),
]

RESOURCE_PREFIXES = {
    "lecture": "课程讲解",
    "quiz": "练习题",
    "mindmap": "思维导图",
    "ppt": "PPT课件",
    "lab": "实操案例",
    "learning_path": "学习路径",
    "assessment": "评估报告",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _known_topic(value: str) -> str:
    for pattern, label in KNOWN_TOPICS:
        if re.search(pattern, value or "", flags=re.IGNORECASE):
            return label
    return ""


def extract_known_topics(value: object) -> list[str]:
    text = _text(value)
    topics: list[str] = []
    for pattern, label in KNOWN_TOPICS:
        if re.search(pattern, text, flags=re.IGNORECASE) and label not in topics:
            topics.append(label)
    return topics


def _paired_topic(value: str) -> str:
    text = value or ""
    labels = extract_known_topics(text)
    for pair, relation_pattern, label in PAIR_TOPIC_RULES:
        pair_close = re.search(r"ipv4.{0,24}ipv6|ipv6.{0,24}ipv4", text, flags=re.IGNORECASE)
        if all(item in labels for item in pair) and (
            pair_close or re.search(relation_pattern, text, flags=re.IGNORECASE)
        ):
            return label
    return ""


def _strip_generation_words(value: str) -> str:
    text = re.split(r"\n\s*HTML课件风格：", value or "", maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip(" ，,。；;！!?？")

    patterns = [
        r"围绕\s*(.+?)(?:(?:，|,|\s)*(?:生成|制作|设计|做一份|写|出)|[。；;！!?？]|$)",
        r"(?:复习|学习|了解|讲讲|讲解|解释)\s*[「《“\"']?(.{2,36}?)[」》”\"']?\s*(?:这个|这一)?(?:知识点|概念|内容|主题)?(?:[，,。；;！!?？]|$)",
        r"(?:什么是|何为)\s*[「《“\"']?(.{2,32}?)[」》”\"']?(?:[，,。；;！!?？]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            text = match.group(1).strip()
            break

    text = re.sub(
        r"^(?:请|帮我|给我|告诉我|我想|我需要|需要|重新|继续|先|再|可以)?\s*"
        r"(?:生成|制作|设计|做一份|写一份|出一套|讲解|解释|复习|学习|了解|围绕|关于|针对)?\s*",
        "",
        text,
    )
    text = re.sub(
        r"(?:这个|这一)?(?:知识点|概念|内容|主题|课程|章节|讲解|练习题|题目|实操案例|实验|HTML课件|课件).*$",
        "",
        text,
    )
    return text.strip(" 「」《》“”\"'[]【】 ，,。；;！!?？")


def normalize_topic(topic: object, fallback: str = "") -> str:
    raw = _text(topic)
    if not raw:
        return fallback

    paired = _paired_topic(raw)
    if paired:
        return paired

    for quoted in re.findall(r"[「《“\"']([^」》”\"']{2,40})[」》”\"']", raw):
        paired = _paired_topic(quoted)
        if paired:
            return paired
        known = _known_topic(quoted)
        if known:
            return known
        cleaned = _strip_generation_words(quoted)
        if 1 < len(cleaned) <= 16:
            return cleaned

    known = _known_topic(raw)
    if known:
        return known

    cleaned = _strip_generation_words(raw)
    paired = _paired_topic(cleaned)
    if paired:
        return paired
    known = _known_topic(cleaned)
    if known:
        return known

    cleaned = re.split(r"[：:，,。；;！!?？\n]", cleaned)[0].strip()
    cleaned = re.sub(r"(功能|原理|概念|定义|特点|作用|意义|优势|缺点|应用场景)$", "", cleaned).strip()
    cleaned = re.sub(r"在.{1,10}中", "", cleaned).strip()
    cleaned = re.sub(r"的.{1,8}$", "", cleaned).strip()
    match = re.match(r"^(\S{2,10})[与和、/]", cleaned)
    if match:
        cleaned = match.group(1).strip()

    if 1 < len(cleaned) <= 16 and not re.search(r"(生成|制作|讲解|练习|需要|帮我|给我)", cleaned):
        return cleaned
    if fallback:
        return fallback
    return cleaned[:12] if cleaned else ""


def topic_from_query(query: object, profile: dict | None = None, fallback: str = "") -> str:
    topic = normalize_topic(query)
    if topic:
        return topic
    if profile:
        for item in profile.get("薄弱知识点", []) or []:
            topic = normalize_topic(item)
            if topic:
                return topic
        return normalize_topic(profile.get("当前课程"), fallback)
    return fallback


def lecture_title_topic(content: object, bundle: dict | None, query: object, profile: dict | None = None) -> str:
    """Use an actual document title or the request topic, never a prose opener."""
    def valid_title(value):
        text = re.sub(r'^[#\s]+|[#\s]+$', '', _text(value))
        if not 2 <= len(text) <= 100:
            return ''
        if re.search(r'^(?:你好|您好|同学|小[A-Za-z一二三四五六七八九十]|好的|当然|下面|接下来|我们|先来)|[。！？\n]', text):
            return ''
        if text in {'讲解', '课程讲解', '文字讲解', '课程内容', '正文', '引言', '概述'}:
            return ''
        return text

    # Only H1 denotes the whole document; numbered subsection headings do not.
    heading = re.search(r'^#\s+(.+?)\s*#*\s*$', _text(content), re.MULTILINE)
    candidates = [heading.group(1) if heading else '', (bundle or {}).get('title')]
    for candidate in candidates:
        title = valid_title(candidate)
        if title:
            return title

    request = re.split(r'用户长期偏好：|HTML课件风格：', _text(query), maxsplit=1)[0]
    topic = re.search(r'(?:主题|知识点|讲授内容)\s*[：:]\s*(.+)', request)
    if topic:
        request = topic.group(1)
    elif '：' in request or ':' in request:
        # Resource instruction and optional school subject precede the topic.
        request = re.split(r'[：:]', request)[-1]
    request = re.split(r'[。；;！!?？\n]', request)[0].strip()
    title = valid_title(request)
    if title and not re.search(r'请|帮我|给我|生成|制作|讲解|解释', title):
        return title
    inferred = valid_title(topic_from_query(query))
    if inferred:
        return inferred
    return valid_title((profile or {}).get('当前课程')) or '当前课程'


def merge_topics(*groups) -> list[str]:
    merged: list[str] = []
    for group in groups:
        values = group if isinstance(group, list) else [group]
        for value in values:
            topic = normalize_topic(value)
            if topic and topic not in merged:
                merged.append(topic)
    return merged


def compact_resource_title(title: object, resource_type: str = "", profile: dict | None = None) -> str:
    raw = _text(title)
    prefix = RESOURCE_PREFIXES.get(resource_type, "")
    candidate = raw

    # Generators already supply a semantic document title. Query-oriented
    # normalization can truncate formulas and mistake subject punctuation for
    # request prefixes; preserve the generated title instead.
    if raw and len(raw) <= 100 and not re.search(r"请|帮我|给我|生成|制作|完整讲授|以下主题", raw):
        for known_prefix in (prefix, "互动教学", "交互教学"):
            if known_prefix:
                candidate = re.sub(r"^(?:" + re.escape(known_prefix) + r"\s*[-—:：]\s*)+", "", candidate)
        return f"{prefix} - {candidate}" if prefix else candidate

    if " - " in raw:
        left, right = raw.split(" - ", 1)
        prefix = prefix or left.strip()
        candidate = right
    elif "：" in raw:
        left, right = raw.split("：", 1)
        prefix = prefix or left.strip()
        candidate = right

    topic = topic_from_query(candidate, profile)
    if topic and prefix:
        return f"{prefix} - {topic}"
    if topic:
        return topic
    return raw[:80]
