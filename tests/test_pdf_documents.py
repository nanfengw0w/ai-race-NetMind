import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from services import pdf_documents as store


class PDFDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE students(id TEXT PRIMARY KEY, owner_id TEXT)')
        self.db.executemany('INSERT INTO students VALUES(?,?)', [('s1', 'u1'), ('s2', 'u2')])
        self.db.commit()
        self.patches = [patch.object(store, 'get_db', return_value=self.db),
                        patch.object(store, 'DOCUMENT_DIR', Path(self.temp.name))]
        for p in self.patches:
            p.start()
        store.init_pdf_documents()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.db.close()
        self.temp.cleanup()

    def make(self, rotation=0, crop=False, blank=False, unicode=False):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=400)
            if not blank:
                page.insert_text((60, 90), 'Repeated phrase')
                page.insert_text((60, 130), 'Repeated phrase')
                if unicode:
                    page.insert_text((60, 170), '中文引用', fontname='china-s')
            if crop:
                page.set_cropbox(fitz.Rect(30, 30, 350, 350))
            page.set_rotation(rotation)
            return store.create_document(pdf.tobytes(), '../safe.pdf', 's1', 'u1')

    def test_exact_repeat_offsets_unicode_and_hash(self):
        info = self.make(unicode=True)
        page = store.get_page(info['id'], 1, 's1', 'u1')
        self.assertIn('中文引用', page['text'])
        start = page['text'].rindex('Repeated phrase')
        ref = dict(document_id=info['id'], content_hash=info['content_hash'], page_number=1,
                   start=start, end=start + len('Repeated phrase'), text='Repeated phrase')
        self.assertEqual(store.resolve_pdf_reference(ref, 's1', 'u1')['start'], start)
        for change in [{'text': 'different'}, {'start': start + 1}, {'content_hash': 'bad'}, {'page_number': 2}, {'end': True}]:
            with self.subTest(change=change), self.assertRaises(store.PDFDocumentError):
                store.resolve_pdf_reference({**ref, **change}, 's1', 'u1')
        for span in page['spans']:
            self.assertEqual(span['text'], page['text'][span['start']:span['end']])
        self.assertEqual(''.join(c['text'] for c in page['chars']), page['text'])
        self.assertEqual(info['title'], 'safe.pdf')

    def test_rotation_crop_and_png_geometry(self):
        for rotation in [0, 90, 180, 270]:
            with self.subTest(rotation=rotation):
                info = self.make(rotation, crop=True)
                page = store.get_page(info['id'], 1, 's1', 'u1')
                pix = fitz.Pixmap(store.get_page(info['id'], 1, 's1', 'u1', image=True))
                self.assertEqual((page['width'], page['height']), (pix.width, pix.height))
                for char in page['chars']:
                    if char['bbox']:
                        x0, y0, x1, y1 = char['bbox']
                        self.assertTrue(0 <= x0 < x1 <= pix.width)
                        self.assertTrue(0 <= y0 < y1 <= pix.height)

    def test_layout_matches_png_for_fractional_crop_and_rotation_without_rendering(self):
        with fitz.open() as pdf:
            for rotation in [0, 90, 180, 270]:
                page = pdf.new_page(width=410.25, height=520.75)
                page.insert_text((70, 100), 'Layout boundaries only')
                page.set_cropbox(fitz.Rect(20.125, 31.75, 380.6, 480.4))
                page.set_rotation(rotation)
            info = store.create_document(pdf.tobytes(), 'layout.pdf', 's1', 'u1')
        expected = []
        for number in range(1, 5):
            pix = fitz.Pixmap(store.get_page(info['id'], number, 's1', 'u1', image=True))
            expected.append({'page_number': number, 'width': pix.width, 'height': pix.height})
        with patch.object(fitz.Page, 'get_pixmap', side_effect=AssertionError('must not render')), \
                patch.object(fitz.Page, 'get_text', side_effect=AssertionError('must not extract text')):
            result = store.get_document_layout(info['id'], 's1', 'u1')
        self.assertEqual(result, {'document_id': info['id'], 'content_hash': info['content_hash'], 'pages': expected})

    def test_layout_permissions_and_original_hash_validation(self):
        info = self.make()
        for student, user in [('s1', 'u2'), ('s2', 'u2'), ('s2', 'u1')]:
            with self.subTest(student=student, user=user), self.assertRaises(store.PDFDocumentError):
                store.get_document_layout(info['id'], student, user)
        store._path(info['id']).write_bytes(b'tampered')
        with self.assertRaises(store.PDFDocumentError) as error:
            store.get_document_layout(info['id'], 's1', 'u1')
        self.assertEqual(error.exception.status_code, 409)

    def test_image_only_can_view_but_not_select(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
            pix.clear_with(255)
            page.insert_image(fitz.Rect(20, 20, 200, 200), stream=pix.tobytes('png'))
            info = store.create_document(pdf.tobytes(), 'scan.pdf', 's1', 'u1')
        page = store.get_page(info['id'], 1, 's1', 'u1')
        self.assertFalse(page['selectable'])
        self.assertEqual(page['text'], '')
        self.assertTrue(store.get_page(info['id'], 1, 's1', 'u1', image=True).startswith(b'\x89PNG'))

    def test_permissions_paths_and_original_tampering(self):
        info = self.make()
        for student, user in [('s1', 'u2'), ('s2', 'u2'), ('s2', 'u1')]:
            with self.assertRaises(store.PDFDocumentError):
                store.get_page(info['id'], 1, student, user)
        for invalid in ['../evil', 'https://example.com/x', 'BAD']:
            with self.assertRaises(store.PDFDocumentError):
                store.get_document(invalid, 's1', 'u1')
        store._path(info['id']).write_bytes(b'tampered')
        with self.assertRaises(store.PDFDocumentError):
            store.get_page(info['id'], 1, 's1', 'u1')
        with self.assertRaises(store.PDFDocumentError):
            store.create_document(b'not a pdf', 'fake.pdf', 's1', 'u1')

    def test_session_detach_is_scoped_transactional_and_foreign_key_safe(self):
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE conversations(id INTEGER PRIMARY KEY,student_id TEXT REFERENCES students(id));
            CREATE TABLE pdf_chat_metadata(conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id),payload_json TEXT);
            INSERT INTO conversations VALUES(1,'s1');
            INSERT INTO conversations VALUES(2,'s2');
            INSERT INTO pdf_chat_metadata VALUES(1,'{}');
            INSERT INTO pdf_chat_metadata VALUES(2,'{}');
        ''')
        first = self.make()
        second = store.create_document(store._path(first['id']).read_bytes(), 'other.pdf', 's2', 'u2')
        first_path, second_path = store._path(first['id']), store._path(second['id'])
        self.db.execute('BEGIN')
        self.assertEqual(store.detach_session_documents('s1'), [first_path])
        self.db.execute('DELETE FROM conversations WHERE student_id=?', ('s1',))
        self.db.execute('DELETE FROM students WHERE id=?', ('s1',))
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(store.get_document(first['id'], 's1', 'u1')['id'], first['id'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM pdf_chat_metadata').fetchone()[0], 2)
        self.assertTrue(first_path.exists())
        self.db.execute('BEGIN')
        self.assertEqual(store.detach_session_documents('s1'), [first_path])
        self.db.execute('DELETE FROM conversations WHERE student_id=?', ('s1',))
        self.db.execute('DELETE FROM students WHERE id=?', ('s1',))
        self.db.commit()
        self.assertIsNone(self.db.execute('SELECT id FROM students WHERE id=?', ('s1',)).fetchone())
        self.assertEqual(store.get_document(second['id'], 's2', 'u2')['id'], second['id'])
        self.assertEqual(self.db.execute('SELECT conversation_id FROM pdf_chat_metadata').fetchone()[0], 2)
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())

    def test_session_detach_supports_optional_missing_tables(self):
        self.db.execute('DROP TABLE pdf_documents')
        self.db.commit()
        self.assertEqual(store.detach_session_documents('s1'), [])

    def test_session_detach_never_returns_non_uuid_paths(self):
        self.db.execute('INSERT INTO pdf_documents(id,user_id,student_id,title,content_hash,page_count,size_bytes) VALUES(?,?,?,?,?,?,?)',
                        ('../escape', 'u1', 's1', 'bad', 'hash', 1, 1))
        self.db.commit()
        self.assertEqual(store.detach_session_documents('s1'), [])
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT id FROM pdf_documents').fetchone()[0], '../escape')


if __name__ == '__main__':
    unittest.main()
