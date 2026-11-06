import { marked } from 'marked'
import test from 'node:test'
import assert from 'node:assert/strict'
import { artifactDownload, artifactEventReceiver, artifactReference, artifactVersion, canReviseArtifact, upsertArtifacts, markdownParagraphs, artifactSelection, artifactSelectionLabel, updatedArtifactReference } from './artifacts.mjs'

test('references pin the viewed historical version, independently of the current version', () => {
  const resource = { id: 21, title: '小说：初稿', version: 2, current_version: 7 }
  const reference = artifactReference(resource)
  assert.deepEqual(reference, { resource_id: 21, title: '小说：初稿', version: 2, current_version: 7 })
  resource.version = 7
  assert.equal(reference.version, 2)
})

test('unpersisted and failed resources cannot be referenced', () => {
  for (const resource of [null, {}, { id: 1, isPlaceholder: true }, ...['pending', 'generating', 'error'].map(status => ({ id: 1, status }))]) {
    assert.equal(artifactReference(resource), null)
  }
  assert.equal(artifactVersion({ id: 1 }), 1)
  assert.equal(artifactVersion({ version: -1 }), 1)
})

test('new versions replace the existing card without duplicates or changing its position', () => {
  const first = { id: 3, version: 1, content: 'first' }
  const second = { id: 8, version: 1, content: 'second' }
  const revised = { id: 8, version: 2, content: 'revised' }
  assert.deepEqual(upsertArtifacts([first, second], [revised]), [first, revised])
  assert.deepEqual(upsertArtifacts([first, revised], [{ ...second, id: '8' }]), [first, revised])
  assert.deepEqual(upsertArtifacts([first], [revised, revised]), [revised, first])
})

test('original downloads preserve content exactly and identify the selected version', () => {
  const content = '# 原稿\r\n\r\n中文、emoji 🌍，保留  两个空格\n'
  const original = artifactDownload({ type: 'document', id: 21, title: '小说/初稿', version: 2, current_version: 7, content })
  assert.equal(original.text, content)
  assert.equal(original.filename, '小说_初稿-v2.md')
  assert.match(original.mime, /charset=utf-8/)
  const html = '<!doctype html><script>window.marker = 1</script>'
  assert.deepEqual(artifactDownload({ type: 'interactive', title: '交互', version: 3, content: html }), {
    filename: '交互-v3.html', text: html, mime: 'application/octet-stream',
  })
  const json = '{"nodes": [1,  2]}\n'
  assert.equal(artifactDownload({ type: 'mindmap', content: json }).text, json)
  assert.match(artifactDownload({ type: 'quiz', content: json }).filename, /\.json$/)
})

test('revision controls are limited to formats supported by the server', () => {
  for (const type of ['document', 'lecture', 'interactive', 'ppt', 'lab', 'assessment']) assert.equal(canReviseArtifact(type), true)
  for (const type of ['quiz', 'mindmap', 'learning_path', 'unknown', undefined]) assert.equal(canReviseArtifact(type), false)
})

test('committed chat artifact and done fallback update each version only once', () => {
  const saved = []
  const actions = []
  const receive = artifactEventReceiver(value => saved.push(value), action => actions.push(action))
  const resource = { id: 5, version: 8, current_version: 8 }
  const event = { action: 'revise', resource, base_version: 7 }
  receive(event)
  receive(event)
  receive({ action: 'restore', resource: { ...resource, version: 9, current_version: 9 }, base_version: 8, restored_from: 2 })
  receive({ action: 'revise' })
  receive({ action: 'unknown', resource })
  assert.deepEqual(saved.map(item => item.version), [8, 9])
  assert.deepEqual(actions, ['revise', 'revise', 'restore'])
  const fallback = []
  artifactEventReceiver(value => fallback.push(value), () => {})(event)
  assert.deepEqual(fallback, [resource])
})

const documentResource = content => ({ id: 21, type: 'document', version: 2, content })

test('paragraph selection uses exact raw Markdown and Unicode code-point offsets for repeated paragraphs', () => {
  const resource = documentResource('# 🌍标题\n\n重复 **🌍**  \n\n重复 **🌍**  \n')
  const { paragraphs, error } = markdownParagraphs(resource)
  assert.equal(error, '')
  assert.equal(paragraphs.length, 2)
  assert.equal(paragraphs[0].text, '重复 **🌍**  ')
  assert.notEqual(paragraphs[0].start, paragraphs[1].start)
  for (const selection of paragraphs) {
    assert.equal(Array.from(resource.content).slice(selection.start, selection.end).join(''), selection.text)
  }
  assert.equal(paragraphs[0].start, Array.from('# 🌍标题\n\n').length)
})

test('paragraph selection ignores unsupported formats', () => {
  for (const resource of [
    { ...documentResource('正文'), type: 'interactive' },
    documentResource('{"content":"正文"}'), documentResource('<html>正文</html>'),
    { ...documentResource('正文'), status: 'generating' },
  ]) assert.equal(markdownParagraphs(resource), null)
  assert.deepEqual(markdownParagraphs(documentResource('# 标题\n\n- 列表\n\n```js\nx()\n```')).paragraphs, [])
})

test('a selection is valid only for its exact artifact version and original paragraph', () => {
  const resource = documentResource('第一段🌍\n\n第二段')
  const selection = markdownParagraphs(resource).paragraphs[1]
  const reference = { ...artifactReference(resource), selection }
  assert.deepEqual(artifactSelection(resource, reference), selection)
  assert.equal(artifactSelection({ ...resource, version: 3 }, reference), null)
  assert.equal(artifactSelection({ ...resource, id: 22 }, reference), null)
  assert.equal(artifactSelection({ ...resource, content: '第一段🌍\n\n已更新' }, reference), null)
  assert.equal(artifactSelection(resource, { ...reference, selection: { ...selection, start: 0 } }), null)
  const updated = updatedArtifactReference({ ...reference, studentId: 'a' }, { ...resource, version: 3 })
  assert.equal(updated.selection, undefined)
  assert.equal(updated.version, 3)
  assert.equal(updated.studentId, 'a')
})

test('CRLF and lone CR lexer mapping retains exact original paragraph text and code-point ranges', () => {
  for (const newline of ['\r\n', '\r']) {
    const prefix = `# 🌍标题${newline}${newline}`
    const paragraph = `重复🌍  ${newline}段内 **原文**  `
    const content = `${prefix}${paragraph}${newline}${newline}${paragraph}${newline}`
    const resource = documentResource(content)
    const result = markdownParagraphs(resource)
    assert.equal(result.error, '')
    assert.equal(result.paragraphs.length, 2)
    const expectedStarts = [Array.from(prefix).length, Array.from(`${prefix}${paragraph}${newline}${newline}`).length]
    for (const [index, selection] of result.paragraphs.entries()) {
      assert.equal(selection.start, expectedStarts[index])
      assert.equal(selection.end, selection.start + Array.from(paragraph).length)
      assert.equal(selection.text, paragraph)
      assert.equal(Array.from(content).slice(selection.start, selection.end).join(''), paragraph)
      assert.deepEqual(artifactSelection(resource, { ...artifactReference(resource), selection }), selection)
    }
    assert.equal(resource.content, content)
  }
})

test('paragraph selection fails closed if lexer tokens skip or rewrite any original span', () => {
  const originalLexer = marked.lexer
  try {
    for (const tokens of [
      [{ type: 'paragraph', raw: '正文' }], // A lexer version that skips definitions.
      [{ type: 'paragraph', raw: '[link]: https://example.com\n\n改写' }],
      [], // Trailing unaccounted source must also fail.
    ]) {
      marked.lexer = () => tokens
      const result = markdownParagraphs(documentResource('[link]: https://example.com\n\n正文'))
      assert.deepEqual(result.paragraphs, [])
      assert.match(result.error, /无法精确定位/)
    }
  } finally { marked.lexer = originalLexer }
})


test('PPT selection accepts server ranges with code points and rejects invalid identity or ranges', () => {
  const resource = { id: 31, version: 2, type: 'ppt', content: '🌍<section>第三页</section>' }
  const selection = { start: 1, end: Array.from(resource.content).length, text: '<section>第三页</section>', slide_number: 3 }
  const reference = { ...artifactReference(resource), selection }
  assert.deepEqual(artifactSelection(resource, reference), selection)
  for (const patch of [{ slide_number: 0 }, { slide_number: 1.5 }, { slide_number: '3' }, { start: -1 }, { end: 100 }, { text: '篡改' }]) {
    assert.equal(artifactSelection(resource, { ...reference, selection: { ...selection, ...patch } }), null)
  }
  assert.equal(artifactSelection({ ...resource, type: 'interactive' }, reference), null)
  assert.equal(artifactSelection({ ...resource, version: 3 }, reference), null)
  assert.equal(artifactSelection({ ...resource, id: 32 }, reference), null)
})

test('active and historical selection labels expose only the slide number for PPT HTML', () => {
  assert.equal(artifactSelectionLabel({ slide_number: 3, text: '<section><script>unsafe()</script></section>' }), '第3页')
  assert.equal(artifactSelectionLabel({ text: '普通🌍段落' }), '所选段落：普通🌍段落')
  assert.equal(artifactSelectionLabel(null), '')
})
