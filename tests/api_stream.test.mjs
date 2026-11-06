import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = (await readFile(new URL('../NetMind Agent/src/services/api.js', import.meta.url), 'utf8'))
  .replace('import.meta.env.VITE_API_BASE', '""');
const { sendResourceMessageStream } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function response(body, status = 200) {
  const bytes = new TextEncoder().encode(body);
  return new Response(new ReadableStream({ start(controller) {
    for (let index = 0; index < bytes.length; index += 2) controller.enqueue(bytes.slice(index, index + 2));
    controller.close();
  } }), { status });
}

test('SSE preserves split UTF-8 and dispatches the completed resource once', async () => {
  const oldFetch = globalThis.fetch;
  const received = [];
  globalThis.fetch = async () => response('event: resource\r\ndata: {"resource":{"content":"蒸发与凝结"}}\r\n\r\nevent: done\r\ndata: {"resource_count":1}\r\n\r\n');
  try {
    await sendResourceMessageStream('水循环', 'test', { onResource: value => received.push(value) });
    assert.equal(received.length, 1);
    assert.equal(received[0].resource.content, '蒸发与凝结');
  } finally { globalThis.fetch = oldFetch; }
});

test('SSE without a final confirmation reports the interruption', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => response('event: status\ndata: {"message":"正在生成"}\n\n');
  try { await assert.rejects(sendResourceMessageStream('test', 'test'), /未收到完成确认/); }
  finally { globalThis.fetch = oldFetch; }
});

test('SSE service errors do not become successful completion', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => response('event: error\ndata: {"message":"模型暂不可用"}\n\n');
  try { await assert.rejects(sendResourceMessageStream('test', 'test'), /模型暂不可用/); }
  finally { globalThis.fetch = oldFetch; }
});

test('HTTP failure is reported before consuming events', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => response('unavailable', 503);
  try { await assert.rejects(sendResourceMessageStream('test', 'test'), /503/); }
  finally { globalThis.fetch = oldFetch; }
});
