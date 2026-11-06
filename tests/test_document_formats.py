import io
from pathlib import Path
from test_pdf_documents import PDFDocumentTests
from services.document_parsers import parse_document
from services import pdf_documents as store


class DocumentFormatsTests(PDFDocumentTests):
    def test_docx_body_order_and_exact_offsets(self):
        from docx import Document
        document = Document()
        document.add_paragraph('before 😀')
        document.add_table(rows=1, cols=2).rows[0].cells[0].text = 'cell'
        document.add_paragraph('after')
        data = io.BytesIO(); document.save(data)
        page = parse_document(data.getvalue(), 'docx')[0]
        self.assertEqual([b['type'] for b in page['blocks']], ['paragraph', 'table', 'paragraph'])
        for block in page['blocks']:
            self.assertEqual(block['text'], page['text'][block['start']:block['end']])
        self.assertLess(page['text'].index('before'), page['text'].index('cell'))
        self.assertLess(page['text'].index('cell'), page['text'].index('after'))

    def test_markdown_unicode_ownership_and_hash(self):
        info = store.create_document('# 中文😀\n\n原文'.encode(), 'source.md', 's1', 'u1')
        page = store.get_page(info['id'], 1, 's1', 'u1')
        self.assertEqual(page['text'], '# 中文😀\n\n原文')
        start = page['text'].index('😀')
        ref = dict(document_id=info['id'], content_hash=info['content_hash'], page_number=1, start=start, end=start+1, text='😀', location_label='evil')
        resolved = store.resolve_pdf_reference(ref, 's1', 'u1')
        self.assertEqual(resolved['location_label'], '第1部分')
        self.assertEqual(resolved['kind'], 'md')
        with self.assertRaises(store.PDFDocumentError):
            store.resolve_pdf_reference(ref, 's2', 'u2')
        store._path(info['id'], 'md').write_bytes(b'tampered')
        with self.assertRaises(store.PDFDocumentError):
            store.resolve_pdf_reference(ref, 's1', 'u1')

    def test_csv_quoted_newline_and_encoding(self):
        page = parse_document('名称,说明\r\n甲,"一行\n二行"\r\n'.encode('gb18030'), 'csv')[0]
        cell = page['blocks'][0]['rows'][1][1]
        self.assertEqual(cell['text'], '一行\n二行')
        self.assertEqual(cell['text'], page['text'][cell['start']:cell['end']])
        self.assertEqual(cell['address'], 'CSV!B2')

    def test_xlsx_sheets_formulas_and_verified_location(self):
        from openpyxl import Workbook
        workbook = Workbook(); workbook.active.title = '数据'
        workbook.active.append(['中文😀', '=1+2'])
        workbook.create_sheet('Other').append(['second'])
        data = io.BytesIO(); workbook.save(data)
        info = store.create_document(data.getvalue(), 'table.xlsx', 's1', 'u1')
        layout = store.get_document_layout(info['id'], 's1', 'u1')
        self.assertEqual(len(layout['pages']), 2)
        page = store.get_page(info['id'], 1, 's1', 'u1')
        cell = page['blocks'][0]['rows'][0][1]
        self.assertEqual(cell['text'], '=1+2')
        ref = dict(document_id=info['id'], content_hash=info['content_hash'], page_number=1, **{k:cell[k] for k in ('start','end','text')})
        self.assertIn('数据!B1', store.resolve_pdf_reference(ref, 's1', 'u1')['location_label'])
        self.assertEqual(store.detach_session_documents('s1'), [store._path(info['id'], 'xlsx')])

    def test_unsupported_and_expansion_limit(self):
        import zipfile
        with self.assertRaisesRegex(store.PDFDocumentError, '.docx'):
            store.create_document(b'old', 'old.doc', 's1', 'u1')
        with self.assertRaises(zipfile.BadZipFile):
            parse_document(b'not a zip', 'xlsx')

    def test_markdown_alias_and_consistent_word_warning(self):
        info = store.create_document('中文.md'.encode(), 'notes.markdown', 's1', 'u1')
        self.assertEqual(info['kind'], 'md')
        from docx import Document
        document = Document(); document.add_paragraph('正文')
        data = io.BytesIO(); document.save(data)
        word = store.create_document(data.getvalue(), 'notes.docx', 's1', 'u1')
        listed = next(d for d in store.list_documents('s1', 'u1') if d['id'] == word['id'])
        self.assertEqual(word['warning'], listed['warning'])
        self.assertIn('图片', word['warning'])

    def test_empty_worksheet_names(self):
        from openpyxl import Workbook
        workbook = Workbook(); workbook.active.title = '空白一'; workbook.create_sheet('空白二')
        data = io.BytesIO(); workbook.save(data)
        pages = parse_document(data.getvalue(), 'xlsx')
        self.assertEqual(len(pages), 2)
        self.assertIn('空白一', pages[0]['label'])
        self.assertIn('空白二', pages[1]['label'])
        self.assertFalse(pages[0]['selectable'])

    def test_xls_saved_types_and_empty_sheet(self):
        import datetime
        try:
            import xlwt
        except ImportError:
            self.skipTest('xlwt is a fixture-only dependency')
        book = xlwt.Workbook(); sheet = book.add_sheet('保存值'); book.add_sheet('空白')
        sheet.write(0, 0, datetime.datetime(2026, 10, 26), xlwt.easyxf(num_format_str='YYYY-MM-DD'))
        sheet.write(0, 1, True); sheet.write(0, 2, False)
        sheet.row(0).set_cell_error(3, 7)
        data = io.BytesIO(); book.save(data)
        pages = parse_document(data.getvalue(), 'xls')
        values = [cell['text'] for cell in pages[0]['blocks'][0]['rows'][0]]
        self.assertEqual(values, ['2026-10-26 00:00:00', 'TRUE', 'FALSE', '#DIV/0!'])
        self.assertIn('空白', pages[1]['label'])

    def test_ooxml_entities_rejected_before_parser(self):
        import zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('word/document.xml', '<!DOCTYPE a [<!ENTITY leak SYSTEM "file:///secret">]><a>&leak;</a>')
        with self.assertRaisesRegex(ValueError, 'XML'):
            parse_document(data.getvalue(), 'docx')

    def test_nonpdf_model_has_location_without_internal_page_number(self):
        import json
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from services import pdf_chat as chat
        self.db.execute('CREATE TABLE conversations(id INTEGER PRIMARY KEY, student_id TEXT, role TEXT, content TEXT)')
        info = store.create_document('非PDF的精确原文'.encode(), 'source.md', 's1', 'u1')
        page = store.get_page(info['id'], 1, 's1', 'u1')
        ref = dict(document_id=info['id'], content_hash=info['content_hash'], page_number=1,
                   start=0, end=len(page['text']), text=page['text'])
        def context(student, pinned, current):
            return [*pinned, {'role':'user','content':current}], {}
        with patch.object(chat, 'get_db', return_value=self.db), patch.object(chat, 'build_context', side_effect=context), patch.object(chat, 'llm_chat', return_value='原文说明。[[S1]]') as model, patch.dict(sys.modules, {'services.chat_service': SimpleNamespace(_prepare_input=lambda m,a:(m,m,{}))}):
            result = chat.answer_pdf_question('解释', 's1', 'u1', ref)
        messages = model.call_args.args[0]
        evidence = json.loads(messages[1]['content'].split('\n',1)[1])['S1']
        self.assertNotIn('page_number', evidence)
        self.assertEqual(evidence['location_label'], '第1部分')
        self.assertIn('原文单元不是打印页', messages[0]['content'])
        self.assertEqual(result['pdf_citations'][0]['page_number'], 1)
        persisted = json.loads(self.db.execute('SELECT payload_json FROM pdf_chat_metadata LIMIT 1').fetchone()[0])
        self.assertEqual(persisted['pdf_citations'][0]['page_number'], 1)

    def test_csv_multiline_selected_cells_are_canonical_intersections(self):
        info = store.create_document('名称,数值,说明\r\n甲,71,"首行说明\r\n第二行说明"\r\n'.encode(), 'cell.csv', 's1', 'u1')
        page = store.get_page(info['id'], 1, 's1', 'u1')
        cell = page['blocks'][0]['rows'][1][2]
        ref = dict(document_id=info['id'], content_hash=info['content_hash'], page_number=1,
                   start=cell['start'], end=cell['end'], text=cell['text'], selected_cells=[{'address':'EVIL','text':'unselected secret'}])
        resolved = store.resolve_pdf_reference(ref, 's1', 'u1')
        self.assertEqual(resolved['selected_cells'], [{'address':'CSV!C2','start':cell['start'],'end':cell['end'],'text':'首行说明\r\n第二行说明'}])
        offset = cell['text'].index('第二')
        partial = {**ref, 'start':cell['start']+offset, 'text':cell['text'][offset:]}
        clipped = store.resolve_pdf_reference(partial, 's1', 'u1')['selected_cells']
        self.assertEqual(clipped, [{'address':'CSV!C2','start':partial['start'],'end':cell['end'],'text':'第二行说明'}])
        self.assertNotIn('首行', str(clipped))
        import json
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from services import pdf_chat as chat
        self.db.execute('CREATE TABLE conversations(id INTEGER PRIMARY KEY,student_id TEXT,role TEXT,content TEXT)')
        with patch.object(chat,'get_db',return_value=self.db), patch.object(chat,'build_context',side_effect=lambda student,pinned,current:(pinned,{})), patch.object(chat,'llm_chat',return_value='说明。[[S1]]') as model, patch.dict(sys.modules, {'services.chat_service':SimpleNamespace(_prepare_input=lambda m,a:(m,m,{}))}):
            result = chat.answer_pdf_question('解释单元格','s1','u1',ref)
        messages = model.call_args.args[0]
        evidence = json.loads(messages[1]['content'].split('\n',1)[1])['S1']
        self.assertEqual(evidence['selected_cells'], resolved['selected_cells'])
        self.assertNotIn('EVIL', str(evidence))
        self.assertIn('同一单元格内的换行仍属于该单元格',messages[0]['content'])
        self.assertNotIn('selected_cells',result['pdf_citations'][0])
        self.assertEqual(result['pdf_reference']['selected_cells'], resolved['selected_cells'])
