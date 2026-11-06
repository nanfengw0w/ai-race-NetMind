"""Immutable, owner-scoped PDF originals and verifiable page selections."""
import hashlib
import re
import uuid
from pathlib import Path

import fitz
from services.document_parsers import KINDS, parse_document
from config import BASE_DIR
from db.database import get_db

DOCUMENT_DIR = Path(BASE_DIR) / 'data' / 'pdf_documents'
MAX_BYTES = 64 * 1024 * 1024
MAX_PAGES = 500
MAX_SELECTION = 20000
SCALE = 1.5
SCHEMA = '''CREATE TABLE IF NOT EXISTS pdf_documents (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, student_id TEXT NOT NULL,
 title TEXT NOT NULL, content_hash TEXT NOT NULL, page_count INTEGER NOT NULL,
 size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(student_id) REFERENCES students(id));
 CREATE INDEX IF NOT EXISTS pdf_documents_owner ON pdf_documents(user_id,student_id);'''


class PDFDocumentError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


def init_pdf_documents():
    db = get_db()
    db.executescript(SCHEMA)
    columns = {r[1] for r in db.execute('PRAGMA table_info(pdf_documents)')}
    if 'kind' not in columns:
        db.execute("ALTER TABLE pdf_documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'pdf'")
    db.commit()


def detach_session_documents(student_id):
    """Detach PDF records within the caller's transaction; unlink only after commit.

    Compatibility with databases without these optional tables is intentional.
    Only canonical UUID-derived paths are ever returned for file cleanup.
    """
    db = get_db()
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('pdf_documents','pdf_chat_metadata','conversations')"
    ).fetchall()}
    if 'pdf_chat_metadata' in tables and 'conversations' in tables:
        db.execute('DELETE FROM pdf_chat_metadata WHERE conversation_id IN '
                   '(SELECT id FROM conversations WHERE student_id=?)', (student_id,))
    paths = []
    if 'pdf_documents' in tables:
        columns = {r[1] for r in db.execute('PRAGMA table_info(pdf_documents)')}
        rows = db.execute("SELECT id," + ("kind" if 'kind' in columns else "'pdf' AS kind") + ' FROM pdf_documents WHERE student_id=?', (student_id,)).fetchall()
        for row in rows:
            try:
                paths.append(_path(row['id'], row['kind']))
            except PDFDocumentError:
                # Corrupted metadata must never authorize arbitrary file deletion.
                continue
        db.execute('DELETE FROM pdf_documents WHERE student_id=?', (student_id,))
    return paths


def _authorize(student_id, user_id):
    if not isinstance(user_id, str) or not user_id or not student_id:
        raise PDFDocumentError(400, '必须提供用户和学习会话')
    row = get_db().execute('SELECT owner_id FROM students WHERE id=?', (student_id,)).fetchone()
    if row is None or row['owner_id'] != user_id:
        raise PDFDocumentError(404, '学习会话不存在')


def _path(document_id, kind='pdf'):
    try:
        if str(uuid.UUID(document_id)) != document_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise PDFDocumentError(400, '无效文档 ID')
    if kind not in KINDS:
        raise PDFDocumentError(400, '无效文档格式')
    return DOCUMENT_DIR / (document_id + '.' + kind)


def get_document(document_id, student_id, user_id):
    _authorize(student_id, user_id)
    _path(document_id)
    row = get_db().execute('SELECT * FROM pdf_documents WHERE id=? AND student_id=? AND user_id=?',
                           (document_id, student_id, user_id)).fetchone()
    if row is None:
        raise PDFDocumentError(404, 'PDF 文档不存在')
    return _document_info(row)


def _document_info(row):
    info = dict(row)
    if info.get('kind') in ('xlsx', 'xls'):
        info['warning'] = '表格不计算公式；XLSX显示原始公式文本，旧版XLS仅提供文件保存的单元格值。'
    elif info.get('kind') == 'docx':
        info['warning'] = 'Word按正文段落和表格顺序阅读，不复现打印分页；图片、浮动对象及复杂排版不在此原文视图显示。'
    return info


def list_documents(student_id, user_id):
    _authorize(student_id, user_id)
    return [_document_info(r) for r in get_db().execute(
        'SELECT * FROM pdf_documents WHERE student_id=? AND user_id=? ORDER BY created_at DESC, id DESC',
        (student_id, user_id)).fetchall()]


def create_document(data, filename, student_id, user_id):
    _authorize(student_id, user_id)
    if not data or len(data) > MAX_BYTES:
        raise PDFDocumentError(413, 'PDF 文件为空或超过64MB')
    kind = Path(filename or 'document.pdf').suffix.lower().lstrip('.')
    if kind == 'markdown':
        kind = 'md'
    if kind == 'doc':
        raise PDFDocumentError(400, '暂不支持旧版 .doc，请在 Word 中另存为 .docx 后上传')
    if kind not in KINDS:
        raise PDFDocumentError(400, '支持 PDF、DOCX、XLSX、XLS、CSV、Markdown 和 TXT')
    if kind != 'pdf':
        try:
            page_count = len(parse_document(data, kind))
        except Exception as exc:
            raise PDFDocumentError(400, '文档无法解析：' + str(exc)) from exc
    try:
        if kind == 'pdf':
            with fitz.open(stream=data, filetype='pdf') as pdf:
                if not pdf.is_pdf or pdf.needs_pass or not 0 < len(pdf) <= MAX_PAGES:
                    raise ValueError()
                page_count = len(pdf)
                # Validate the page tree before persisting an unreadable original.
                for page in pdf:
                    if (page.rect.is_empty or max(page.rect.width, page.rect.height) > 10000
                            or page.rect.width * page.rect.height * SCALE * SCALE > 25000000):
                        raise ValueError()
    except Exception as exc:
        raise PDFDocumentError(400, '需要有效、未加密且不超过500页的PDF') from exc
    document_id = str(uuid.uuid4())
    title = re.sub(r'[\x00-\x1f\x7f]', '', (filename or 'document.pdf').replace('\\', '/').split('/')[-1])[:180] or 'document.pdf'
    digest = hashlib.sha256(data).hexdigest()
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(document_id, kind)
    path.write_bytes(data)
    db = get_db()
    try:
        db.execute('INSERT INTO pdf_documents(id,user_id,student_id,title,content_hash,page_count,size_bytes,kind) VALUES(?,?,?,?,?,?,?,?)',
                   (document_id, user_id, student_id, title, digest, page_count, len(data), kind))
        db.commit()
    except BaseException:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return get_document(document_id, student_id, user_id)


def _open(document_id, student_id, user_id, page_number):
    info = get_document(document_id, student_id, user_id)
    if type(page_number) is not int or not 1 <= page_number <= info['page_count']:
        raise PDFDocumentError(400, '无效页码')
    try:
        data = _path(document_id, info.get('kind', 'pdf')).read_bytes()
    except OSError as exc:
        raise PDFDocumentError(404, 'PDF 原文件不可用') from exc
    if hashlib.sha256(data).hexdigest() != info['content_hash']:
        raise PDFDocumentError(409, 'PDF 原文件校验失败')
    if info.get('kind', 'pdf') != 'pdf':
        try:
            return info, parse_document(data, info['kind'])
        except Exception as exc:
            raise PDFDocumentError(400, '文档无法解析：' + str(exc)) from exc
    return info, fitz.open(stream=data, filetype='pdf')


def get_document_layout(document_id, student_id, user_id):
    """Read rotated crop boundaries only, with the rasterizer's integer bounds."""
    info, pdf = _open(document_id, student_id, user_id, 1)
    if isinstance(pdf, list):
        return {'document_id': document_id, 'content_hash': info['content_hash'], 'kind': info['kind'],
                'pages': [{k: page[k] for k in ('page_number', 'label')} for page in pdf]}
    with pdf:
        matrix = fitz.Matrix(SCALE, SCALE)
        pages = []
        for index, page in enumerate(pdf):
            # page.rect already reflects cropbox and page rotation. IRect uses
            # MuPDF's own rounding tolerance, matching get_pixmap(matrix=...).
            bounds = (page.rect * matrix).irect
            pages.append({'page_number': index + 1, 'width': bounds.width, 'height': bounds.height})
    return {'document_id': document_id, 'content_hash': info['content_hash'], 'pages': pages}


def get_page(document_id, page_number, student_id, user_id, image=False):
    info, pdf = _open(document_id, student_id, user_id, page_number)
    if isinstance(pdf, list):
        if image:
            raise PDFDocumentError(400, '此格式没有原始页图像，请使用结构化原文')
        return {'document_id': document_id, 'content_hash': info['content_hash'], 'kind': info['kind'], **pdf[page_number - 1]}
    with pdf:
        page = pdf[page_number - 1]
        matrix = fitz.Matrix(SCALE, SCALE)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        if image:
            return pix.tobytes('png')
        chars, spans, parts = [], [], []
        offset = 0
        def box(bounds):
            rect = fitz.Rect(bounds) * page.rotation_matrix * matrix
            return [rect.x0 - pix.x, rect.y0 - pix.y, rect.x1 - pix.x, rect.y1 - pix.y]
        for block in page.get_text('rawdict', sort=False)['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    start = offset
                    text = ''.join(c['c'] for c in span['chars'])
                    for char in span['chars']:
                        for codepoint in char['c']:
                            chars.append({'text': codepoint, 'start': offset, 'end': offset + 1, 'bbox': box(char['bbox'])})
                            offset += 1
                    parts.append(text)
                    if text:
                        spans.append({'text': text, 'start': start, 'end': offset, 'bbox': box(span['bbox']), 'font_size': span['size'] * SCALE, 'dir': list(line['dir'])})
                parts.append('\n')
                chars.append({'text': '\n', 'start': offset, 'end': offset + 1, 'bbox': None})
                offset += 1
        text = ''.join(parts)
        return {'document_id': document_id, 'content_hash': info['content_hash'], 'page_number': page_number,
                'kind': 'pdf', 'label': f'第{page_number}页', 'width': pix.width, 'height': pix.height, 'rotation': page.rotation,
                'text': text, 'spans': spans, 'chars': chars, 'selectable': bool(text.strip()),
                'text_hash': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def resolve_pdf_reference(reference, student_id, user_id):
    if not isinstance(reference, dict):
        raise PDFDocumentError(400, 'PDF 引用必须是对象')
    document_id = reference.get('document_id')
    info = get_document(document_id, student_id, user_id)
    if reference.get('content_hash') != info['content_hash']:
        raise PDFDocumentError(409, 'PDF 引用哈希不匹配')
    page = get_page(document_id, reference.get('page_number'), student_id, user_id)
    start, end, selected = reference.get('start'), reference.get('end'), reference.get('text')
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(page['text'])
            or end - start > MAX_SELECTION or not isinstance(selected, str) or not selected.strip()
            or selected != page['text'][start:end]):
        raise PDFDocumentError(400, 'PDF 选段范围或逐字内容不匹配')
    selected_cells = []
    for block_index, block in enumerate((b for b in page.get('blocks', []) if b['type'] == 'table'), 1):
        for row_index, row in enumerate(block.get('rows', []), 1):
            for column_index, cell in enumerate(row, 1):
                a, b = max(start, cell['start']), min(end, cell['end'])
                if a < b:
                    selected_cells.append({
                        'address': cell.get('address') or f'表格{block_index} · 第{row_index}行第{column_index}列',
                        'start': a, 'end': b, 'text': page['text'][a:b]})
    addresses = [cell['address'] for block in page.get('blocks', []) for row in block.get('rows', []) for cell in row
                 if cell.get('address') and cell['end'] > start and cell['start'] < end]
    location = page['label'] + ((' · ' + addresses[0] + ('–' + addresses[-1] if len(addresses) > 1 else '')) if addresses else '')
    return {'selected_cells': selected_cells, 'kind': info.get('kind', 'pdf'), 'location_label': location, 'document_id': document_id, 'content_hash': info['content_hash'], 'page_number': page['page_number'],
            'start': start, 'end': end, 'text': selected, 'title': info['title'], 'selection_type': 'pdf',
            'context': page['text'][max(0, start - 300):min(len(page['text']), end + 300)]}
