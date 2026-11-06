import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { selectionReference, spanHighlight, pdfLocationError } from './pdfSelection.mjs';
test('precise repeated-text and emoji selection', () => {
 const dom = new JSDOM('<div id="page"><span data-pdf-start="0">重复😀</span><span data-pdf-start="4">重复😀</span></div>');
 const root = dom.window.document.querySelector('#page');
 const range = dom.window.document.createRange(); range.setStart(root.lastChild.firstChild, 0); range.setEnd(root.lastChild.firstChild, 4);
 const selection = dom.window.getSelection(); selection.addRange(range);
 assert.deepEqual(selectionReference(root, selection, { text: '重复😀\n重复😀', page_number: 2, content_hash: 'hash' }, { id: 'doc' }), { title: 'PDF 原文', document_id: 'doc', content_hash: 'hash', page_number: 2, start: 4, end: 7, text: '重复😀' });
});
test('cross-page selection rejected', () => {
 const dom = new JSDOM('<div id="page"><span data-pdf-start="0">甲</span></div><span>乙</span>');
 const root = dom.window.document.querySelector('#page');
 const range = dom.window.document.createRange(); range.setStart(root.firstChild.firstChild, 0); range.setEnd(root.nextSibling.firstChild, 1);
 const selection = dom.window.getSelection(); selection.addRange(range);
 assert.equal(selectionReference(root, selection, { text: '甲', page_number: 1 }, { id: 'doc' }), null);
});
test('highlight respects code points', () => {
 assert.deepEqual(spanHighlight({ start: 4, end: 8, text: '甲😀乙丙' }, { start: 5, end: 7 }), { before: '甲', selected: '😀乙', after: '丙' });
 assert.equal(spanHighlight({ start: 4, end: 8, text: '甲😀乙丙' }, { start: 8, end: 9 }), null);
});

test('invalid source hash, range or text is reported without guessing', () => {
 const page = { page_number: 1, content_hash: 'new', text: '甲😀乙' };
 assert.match(pdfLocationError(page, { page_number: 1, content_hash: 'old', start: 0, end: 1 }), /版本不匹配/);
 assert.match(pdfLocationError(page, { page_number: 1, start: 0, end: 4 }), /范围无效/);
 assert.match(pdfLocationError(page, { page_number: 1, start: 0, end: 1, text: '乙' }), /文字.*不匹配/);
 assert.equal(pdfLocationError(page, { page_number: 1, start: 1, end: 2, text: '😀' }), '');
});
