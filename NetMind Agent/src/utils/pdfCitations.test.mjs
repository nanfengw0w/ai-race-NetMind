import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { mountPdfCitations } from './pdfCitations.mjs'

test('PDF citations resolve only allocated IDs and retain safe text attributes', () => {
  const dom = new JSDOM('<div id="root"><p>答案[[S1]]，未知[[S7]]</p><code>[[S1]]</code></div>')
  const root = dom.window.document.getElementById('root')
  mountPdfCitations(root, [{ id: 'S1', page_number: 2, title: '<img onerror=alert(1)>', text: '逐字原文' }], 'source')
  assert.equal(root.querySelectorAll('button').length, 1)
  assert.equal(root.querySelector('button').dataset.pdfCitation, 'S1')
  assert.equal(root.querySelector('button').textContent, '第 2 页 ↗')
  assert.match(root.textContent, /\[\[S7\]\]/)
  assert.equal(root.querySelector('code').textContent, '[[S1]]')
  assert.equal(root.querySelectorAll('img').length, 0)
})
