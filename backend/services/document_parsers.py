"""Bounded original-document parsers. All offsets index Python Unicode text."""
import csv
import io
import zipfile
from pathlib import Path

KINDS = {'pdf', 'docx', 'xlsx', 'xls', 'csv', 'md', 'txt'}
MAX_TEXT = 5_000_000
MAX_CELLS = 200_000
MAX_UNITS = 500


def _zip_check(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > 128 * 1024 * 1024:
            raise ValueError('压缩文档展开后过大，请拆分后上传')
        if any(e.flag_bits & 1 for e in entries):
            raise ValueError('不支持加密文档，请先解除密码')
        # OOXML does not require DTDs/entities. Reject them before either XML
        # library sees input (including UTF-16/32 markup), never fetch links.
        for entry in entries:
            if entry.filename.lower().endswith(('.xml', '.rels')):
                xml = archive.read(entry).replace(b'\x00', b'').upper()
                if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
                    raise ValueError('文档包含不支持的 XML 实体声明，请另存为新文档')


def _decode(data):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            value = data.decode(encoding)
            if '\x00' in value:
                raise ValueError('文本文档包含不支持的二进制内容，请另存为 UTF-8')
            return value
        except UnicodeDecodeError:
            pass
    raise ValueError('无法识别文本编码，请另存为 UTF-8 后上传')


def parse_document(data, kind):
    kind = 'md' if kind == 'markdown' else kind
    pages, total, cells = [], 0, 0
    def unit(label, source_blocks):
        nonlocal total, cells
        parts, blocks, offset = [], [], 0
        for source in source_blocks:
            if blocks:
                parts.append('\n'); offset += 1
            start = offset
            block = {'type': source['type'], 'start': start}
            if source['type'] == 'table':
                rows = []
                for row in source['rows']:
                    output = []
                    for index, cell in enumerate(row):
                        cells += 1
                        if cells > MAX_CELLS:
                            raise ValueError('表格超过20万单元格，请拆分后上传')
                        if index:
                            parts.append('\t'); offset += 1
                        text, address = cell
                        output.append({'text': text, 'start': offset, 'end': offset + len(text), **({'address': address} if address else {})})
                        parts.append(text); offset += len(text)
                    rows.append(output)
                    parts.append('\n'); offset += 1
                block['rows'] = rows
            else:
                text = source['text']
                parts.append(text); offset += len(text)
                if 'level' in source:
                    block['level'] = source['level']
            block['end'] = offset
            block['text'] = ''.join(parts)[start:offset]
            blocks.append(block)
        text = ''.join(parts)
        total += len(text)
        if total > MAX_TEXT or len(pages) >= MAX_UNITS:
            raise ValueError('文档内容过大，请拆分后上传')
        pages.append({'page_number': len(pages) + 1, 'label': label, 'text': text, 'blocks': blocks, 'selectable': bool(text.strip())})
    if kind in ('md', 'txt'):
        text = _decode(data)
        # Preserve raw source, including newlines; units are source chunks, not printed pages.
        for start in range(0, len(text), 16000):
            unit(f'第{len(pages) + 1}部分', [{'type': 'paragraph', 'text': text[start:start + 16000]}])
    elif kind == 'docx':
        _zip_check(data)
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        document = Document(io.BytesIO(data))
        batch, size = [], 0
        for child_index, child in enumerate(document.element.body.iterchildren()):
            if child_index > 100000:
                raise ValueError('Word段落过多，请拆分后上传')
            name = child.tag.rsplit('}', 1)[-1]
            if name == 'p':
                p = Paragraph(child, document)
                style = p.style.name if p.style else ''
                block = {'type': 'heading' if style.startswith('Heading') else 'paragraph', 'text': p.text}
                if block['type'] == 'heading':
                    block['level'] = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
                weight = len(p.text)
            elif name == 'tbl':
                table = Table(child, document)
                if len(table.rows) > 10000 or len(table.rows) * len(table.columns) > MAX_CELLS:
                    raise ValueError('Word表格过大，请拆分后上传')
                rows = [[(c.text, None) for c in r.cells] for r in table.rows]
                block = {'type': 'table', 'rows': rows}
                weight = sum(len(c[0]) for r in rows for c in r)
            else:
                continue
            if size + weight > 16000 and batch:
                unit(f'第{len(pages) + 1}部分', batch); batch, size = [], 0
            batch.append(block); size += weight
        if batch:
            unit(f'第{len(pages) + 1}部分', batch)
    else:
        def sheet(name, rows):
            batch, first, last = [], 1, 0
            for number, row in enumerate(rows, 1):
                if number > 100000:
                    raise ValueError('工作表超过10万行，请拆分后上传')
                values = []
                if len(row) > 2048:
                    raise ValueError('工作表超过2048列，请拆分后上传')
                for col, value in enumerate(row, 1):
                    letters, n = '', col
                    while n:
                        n, rem = divmod(n - 1, 26); letters = chr(65 + rem) + letters
                    values.append(('' if value is None else str(value), f'{name}!{letters}{number}'))
                batch.append(values); last = number
                if len(batch) == 100:
                    unit(f'工作表 {name} · 第 {first}–{last} 行', [{'type': 'table', 'rows': batch}]); batch, first = [], last + 1
            if batch:
                unit(f'工作表 {name} · 第 {first}–{last} 行', [{'type': 'table', 'rows': batch}])
            elif last == 0:
                unit(f'工作表 {name} · 空工作表', [])
        if kind == 'csv':
            csv.field_size_limit(MAX_TEXT)
            sheet('CSV', csv.reader(io.StringIO(_decode(data), newline=''), strict=True))
        elif kind == 'xlsx':
            _zip_check(data)
            import openpyxl
            workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
            try:
                for ws in workbook.worksheets:
                    if ws.max_row and ws.max_column and ws.max_row * ws.max_column > MAX_CELLS:
                        raise ValueError('工作表范围超过20万单元格，请清理空白格式或拆分后上传')
                    sheet(ws.title, ws.iter_rows(values_only=True))
            finally:
                workbook.close()
        elif kind == 'xls':
            import xlrd
            workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
            try:
                for ws in workbook.sheets():
                    if ws.nrows * ws.ncols > MAX_CELLS:
                        raise ValueError('工作表超过20万单元格，请拆分后上传')
                    def saved_value(cell):
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            date = xlrd.xldate_as_datetime(cell.value, workbook.datemode)
                            return date.time().isoformat() if 0 <= cell.value < 1 else date.isoformat(sep=' ')
                        if cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            return 'TRUE' if cell.value else 'FALSE'
                        if cell.ctype == xlrd.XL_CELL_ERROR:
                            return xlrd.error_text_from_code.get(cell.value, f'#ERROR({cell.value})')
                        if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                            return ''
                        return cell.value
                    sheet(ws.name, ([saved_value(cell) for cell in ws.row(i)] for i in range(ws.nrows)))
            finally:
                workbook.release_resources()
        else:
            raise ValueError('不支持此文档格式')
    if not pages:
        unit('第1部分', [])
    return pages
