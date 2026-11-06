import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = (await readFile(new URL('../services/api.js', import.meta.url), 'utf8'))
  .replace('import.meta.env.VITE_API_BASE', '""')
const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

test('chat and generation requests send the pinned reference and context events preserve message IDs', async () => {
  const original = globalThis.fetch
  const requests = []
  const events = []
  globalThis.fetch = async (_url, options) => {
    requests.push(JSON.parse(options.body))
    return new Response('event: context\ndata: {"references":[{"resource_id":5,"version":2,"title":"小说"}]}\n\nevent: done\ndata: {"conversation_id":92}\n\n')
  }
  try {
    const reference = { resource_id: 5, version: 2, current_version: 7, selection: { start: 3, end: 6, text: '段落🌍' } }
    await api.sendChatMessageStream('续写', 'session-a', [], {
      onContext: value => events.push(value), onDone: value => events.push(value),
    }, 'user-a', [], reference)
    await api.sendResourceMessageStream('做课件', 'session-a', {}, 'user-a', [], ['ppt'], reference)
    assert.deepEqual(requests[0].history, [])
    assert.deepEqual(requests.map(item => item.resource_ref), [reference, reference])
    assert.equal(events[0].references[0].version, 2)
    assert.equal(events[1].conversation_id, 92)
  } finally { globalThis.fetch = original }
})

test('artifact APIs preserve ownership, selected version, optimistic concurrency and persisted message IDs', async () => {
  const original = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, body: options.body ? JSON.parse(options.body) : null })
    return Response.json({ id: 12, version: 4, content: 'new content' })
  }
  try {
    await api.getArtifact(12, '会话 A', 'owner', 2)
    await api.restoreArtifact(12, '会话 A', 'owner', 2, 3)
    await api.reviseArtifact(12, '会话 A', 'owner', 2, 3, '修改结尾')
    await api.saveMessageArtifact('会话 A', 'owner', 92)
    const query = new URL(requests[0].url, 'http://localhost').searchParams
    assert.equal(query.get('student_id'), '会话 A')
    assert.equal(query.get('user_id'), 'owner')
    assert.equal(query.get('version'), '2')
    assert.deepEqual(requests[1].body, { student_id: '会话 A', user_id: 'owner', version: 2, expected_version: 3 })
    assert.deepEqual(requests[2].body, { student_id: '会话 A', user_id: 'owner', base_version: 2, expected_version: 3, instruction: '修改结尾' })
    assert.equal(requests[3].body.conversation_id, 92)
  } finally { globalThis.fetch = original }
})

test('revision conflicts retain the server detail for a useful Chinese error message', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => Response.json({ detail: '作品已更新，请先查看最新版本。' }, { status: 409 })
  try { await assert.rejects(api.reviseArtifact(12, 'a', 'u', 2, 3, '重写'), /作品已更新/) }
  finally { globalThis.fetch = original }
})

test('single-slide restore pins the historical source and latest base independently', async () => {
  const original = globalThis.fetch
  const requests = []
  const restored = { id: 'a/b', version: 5, metadata: { restore_scope: {
    type: 'slide', slide_number: 3, source_version: 1, base_version: 4,
  } } }
  globalThis.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) })
    return Response.json(restored)
  }
  try {
    assert.deepEqual(await api.restoreArtifact('a/b', '会话', 'owner', 1, 4, 3), restored)
    assert.equal(new URL(requests[0].url, 'http://localhost').pathname, '/api/artifacts/a%2Fb/restore')
    assert.deepEqual(requests[0].body, {
      student_id: '会话', user_id: 'owner', version: 1, expected_version: 4, slide_number: 3,
    })
    globalThis.fetch = async () => Response.json({ id: 'a/b', version: 4, unchanged: true })
    assert.deepEqual(await api.restoreArtifact('a/b', '会话', 'owner', 1, 4, 3), {
      id: 'a/b', version: 4, unchanged: true,
    })
    globalThis.fetch = async () => Response.json({ detail: '作品已更新，请先查看最新版本。' }, { status: 409 })
    await assert.rejects(api.restoreArtifact('a/b', '会话', 'owner', 1, 4, 3), /作品已更新/)
  } finally { globalThis.fetch = original }
})

test('talk SSE delivers committed artifact and its done fallback unchanged', async () => {
  const original = globalThis.fetch
  const artifact = { action: 'restore', resource: { id: 5, version: 8, current_version: 8, content: 'restored' }, base_version: 7, restored_from: 2 }
  const events = []
  globalThis.fetch = async () => new Response(`event: artifact\ndata: ${JSON.stringify(artifact)}\n\nevent: done\ndata: ${JSON.stringify({ artifact, conversation_id: 99 })}\n\n`)
  try {
    await api.sendChatMessageStream('restore', 'a', [], {
      onArtifact: value => events.push(value), onDone: value => events.push(value.artifact),
    })
    assert.deepEqual(events, [artifact, artifact])
  } finally { globalThis.fetch = original }
})


test('talk SSE rejects normal EOF without done with version verification guidance', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => new Response('event: delta\ndata: {"text":"部分回答"}\n\n')
  try {
    await assert.rejects(api.sendChatMessageStream('修改结尾', 'a'), /连接提前中断.*请刷新核实作品版本后再操作/)
  } finally { globalThis.fetch = original }
})

test('talk SSE retains a committed artifact callback but rejects EOF without done', async () => {
  const original = globalThis.fetch
  const artifact = { action: 'revise', resource: { id: 5, version: 8, content: 'saved' } }
  const received = []
  globalThis.fetch = async () => new Response(`event: artifact\ndata: ${JSON.stringify(artifact)}\n\n`)
  try {
    await assert.rejects(api.sendChatMessageStream('修改结尾', 'a', [], {
      onArtifact: value => received.push(value),
    }), /作品可能已保存.*请刷新核实作品版本后再操作/)
    assert.deepEqual(received, [artifact])
  } finally { globalThis.fetch = original }
})

test('talk SSE server error takes precedence over missing done', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => new Response('event: error\ndata: {"message":"服务端明确错误"}\n\n')
  try {
    await assert.rejects(api.sendChatMessageStream('修改结尾', 'a'), { message: '服务端明确错误' })
  } finally { globalThis.fetch = original }
})

test('revision API sends the exact selected paragraph without altering its raw text', async () => {
  const original = globalThis.fetch
  let body
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body)
    return Response.json({ id: 12, version: 4 })
  }
  try {
    const selection = { start: 8, end: 16, text: '**🌍**段落  ' }
    await api.reviseArtifact(12, 'a', 'u', 2, 3, '重写所选段落', selection)
    assert.deepEqual(body.selection, selection)
    assert.equal(body.base_version, 2)
    assert.equal(body.expected_version, 3)
  } finally { globalThis.fetch = original }
})


test('slide listing pins ownership/version and slide revisions keep server selection unchanged', async () => {
  const original = globalThis.fetch
  const requests = []
  const selection = { start: 1, end: 5, text: '<🌍/>', slide_number: 3 }
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, body: options.body ? JSON.parse(options.body) : null })
    return Response.json([{ number: 3, title: '第三页', selection }])
  }
  try {
    assert.deepEqual(await api.getArtifactSlides('a/b', '会话', 'owner', 2), [{ number: 3, title: '第三页', selection }])
    const url = new URL(requests[0].url, 'http://localhost')
    assert.equal(url.pathname, '/api/artifacts/a%2Fb/slides')
    assert.equal(url.searchParams.get('student_id'), '会话')
    assert.equal(url.searchParams.get('user_id'), 'owner')
    assert.equal(url.searchParams.get('version'), '2')
    await api.reviseArtifact('a/b', '会话', 'owner', 2, 4, '调整第三页', selection)
    assert.deepEqual(requests[1].body.selection, selection)
    assert.equal(requests[1].body.expected_version, 4)
    globalThis.fetch = async () => Response.json({ detail: '页面无法安全定位' }, { status: 400 })
    await assert.rejects(api.getArtifactSlides('a/b', '会话', 'owner', 2), /页面无法安全定位/)
  } finally { globalThis.fetch = original }
})
