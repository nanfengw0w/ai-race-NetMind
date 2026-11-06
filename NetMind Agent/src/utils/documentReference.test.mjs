import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { selectionReference } from './pdfSelection.mjs';
import { documentKindLabel, documentLocationLabel } from './documentReference.mjs';

test('document labels preserve canonical worksheet location', () => {
 assert.equal(documentKindLabel({ kind: 'docx' }), 'Word');
 assert.equal(documentLocationLabel({ kind: 'xlsx', page_number: 2, location_label: '工作表 实验 · 第 101–200 行' }), '工作表 实验 · 第 101–200 行');
 assert.equal(documentLocationLabel({ kind: 'md', page_number: 3 }), '第 3 部分');
 assert.equal(documentLocationLabel({ page_number: 3 }), '第 3 页');
});

test('raw markdown source is selectable literally with Unicode offsets', () => {
 const dom = new JSDOM('<article><span data-pdf-start="0"></span></article>');
 const root = dom.window.document.querySelector('article');
 const raw = '# 😀\n<script>alert(1)</script>';
 root.firstChild.textContent = raw;
 const range = dom.window.document.createRange();
 range.setStart(root.firstChild.firstChild, 2); range.setEnd(root.firstChild.firstChild, raw.length);
 const selection = dom.window.getSelection(); selection.addRange(range);
 const ref = selectionReference(root, selection, { text: raw, page_number: 1, content_hash: 'hash' }, { id: 'md', title: '原始 Markdown' });
 assert.equal(ref.start, 2);
 assert.equal(ref.end, Array.from(raw).length);
 assert.equal(ref.text, '😀\n<script>alert(1)</script>');
 assert.equal(root.querySelector('script'), null);
});

test('table selection preserves canonical separator and literal formula', () => {
 const dom = new JSDOM('<article><span data-pdf-start="0">😀</span><span data-pdf-start="2">=SUM(A1:A2)</span></article>');
 const root = dom.window.document.querySelector('article');
 const range = dom.window.document.createRange(); range.setStart(root.firstChild.firstChild, 0); range.setEnd(root.lastChild.firstChild, 11);
 const selection = dom.window.getSelection(); selection.addRange(range);
 const ref = selectionReference(root, selection, { text: '😀\t=SUM(A1:A2)', page_number: 2 }, { id: 'sheet' });
 assert.equal(ref.text, '😀\t=SUM(A1:A2)');
});

test('spreadsheet selection preview identifies overlapping addressed cells', async () => {
 const { documentSelectionLabel } = await import('./documentReference.mjs');
 const page = { label: '工作表 实验', blocks: [{ type: 'table', rows: [[{ text: '😀', start: 0, end: 1, address: '实验!A1' }, { text: '', start: 2, end: 2, address: '实验!B1' }, { text: '=1+1', start: 3, end: 7, address: '实验!C1' }]] }] };
 assert.equal(documentSelectionLabel(page, { start: 0, end: 1 }), '工作表 实验 · 实验!A1');
 assert.equal(documentSelectionLabel(page, { start: 0, end: 7 }), '工作表 实验 · 实验!A1–实验!C1');
 assert.equal(documentSelectionLabel({ label: '空表', blocks: [] }, { start: 0, end: 1 }), '空表');
});
