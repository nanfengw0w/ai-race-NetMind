import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { sanitizeHtml } from './markdown.js'
import { parseMarkdownMath, renderMarkdownMath } from './markdownMath.mjs'
import { markdownParagraphs, artifactSelection } from './artifacts.mjs'

test('user HTML stays sanitized when trusted local math is inserted afterwards', () => {
  const { window } = new JSDOM('')
  const parsed = parseMarkdownMath(String.raw`<img src=x onerror="alert(1)" style="position:fixed"><script>alert(1)</script><svg><a xlink:href="javascript:alert(1)">bad</a></svg><a href="java&#10;script:alert(1)">bad</a><style>body{display:none}</style>

电压 \(U=IR\)，危险指令 \(\href{javascript:alert(1)}{click}\)。

` + '\n`$literal$`\n' + String.raw`

<code>\(literal\)</code>`)
  const element = window.document.createElement('div')
  element.innerHTML = sanitizeHtml(parsed.html, window)
  renderMarkdownMath(element, parsed.formulas)
  assert.equal(element.querySelectorAll('.katex').length, 2)
  assert.equal(element.querySelector('script, svg, style, iframe, object, embed, [onerror]'), null)
  assert.equal(element.querySelector('img').hasAttribute('style'), false)
  for (const link of element.querySelectorAll('a')) assert.doesNotMatch(link.getAttribute('href') || '', /javascript/i)
  assert.deepEqual([...element.querySelectorAll('code')].map(node => node.textContent), ['$literal$', String.raw`\(literal\)`])
  assert.ok(element.querySelector('.katex [style]'), 'only generated math retains layout styles')
  window.close()
})

test('rendered document paragraphs still bind the exact version and Unicode source', () => {
  const content = '😀 电压 \\(U=IR\\)，<b>原文</b>保留。\r\n\r\n功率 $P=UI$。'
  const resource = { id: 12, type: 'lecture', version: 3, content }
  const original = markdownParagraphs(resource)
  const { window } = new JSDOM('')
  const { html, formulas } = parseMarkdownMath(content)
  const element = window.document.createElement('div')
  element.innerHTML = sanitizeHtml(html, window)
  renderMarkdownMath(element, formulas)
  assert.equal(element.querySelectorAll('.katex').length, 2)
  assert.equal(resource.content, content)
  assert.deepEqual(markdownParagraphs(resource), original)
  for (const selection of original.paragraphs) {
    assert.equal(Array.from(content).slice(selection.start, selection.end).join(''), selection.text)
    assert.deepEqual(artifactSelection(resource, { resource_id: 12, version: 3, selection }), selection)
    assert.equal(artifactSelection(resource, { resource_id: 12, version: 4, selection }), null)
  }
  window.close()
})
