import test from 'node:test'
import assert from 'node:assert/strict'
import { failedResource, finishGeneration, generationOutcome, generationSummary, resourceLifecycleTitle } from './resourceGeneration.mjs'

const pending = (type = 'interactive') => ({ type, status: 'pending', title: '互动教学生成中', content: '', isPlaceholder: true })
const ready = id => ({ id, type: 'lecture', status: 'ready', title: '已有讲解', content: '完整内容', isPlaceholder: false })

test('all failures finish the round without a success claim or an in-progress title', () => {
  const error = failedResource(pending(), { message: '互动教学未能生成可用内容，请重新发送问题重试。', detail: '静态检查没有通过。' })
  const resources = [error]
  assert.deepEqual(generationOutcome(resources, 0), { succeeded: 0, failed: 1, unfinished: 0, planned: 1 })
  assert.match(generationSummary(resources, 0), /未能生成可用/)
  assert.doesNotMatch(generationSummary(resources, 0), /已完成|0 个/)
  assert.equal(resourceLifecycleTitle(error), '互动教学生成未完成')
  assert.doesNotMatch(resourceLifecycleTitle(error), /生成中|互动教学.*互动教学/)
  assert.equal(error.errorMessage, '互动教学未能生成可用内容，请重新发送问题重试。')
  assert.equal(error.errorDetail, '静态检查没有通过。')
})

test('partial success keeps usable content and reports both results', () => {
  const completed = ready(2)
  const resources = finishGeneration([completed, pending()], 0)
  assert.strictEqual(resources[0], completed)
  assert.equal(resources[1].status, 'error')
  assert.deepEqual(generationOutcome(resources, 0), { succeeded: 1, failed: 1, unfinished: 0, planned: 2 })
  assert.match(generationSummary(resources, 0), /已完成 1 个.*1 个未能完成/)
})

test('all-success and older backend counts use only this round, excluding history', () => {
  const history = [ready(10), ready(11), ready(12)]
  const resources = [...history, ready(20), ready(21)]
  assert.deepEqual(generationOutcome(resources, history.length), { succeeded: 2, failed: 0, unfinished: 0, planned: 2 })
  assert.equal(generationSummary(resources, history.length, { resource_count: 99 }), '已完成 2 个学习资源，右侧工作台已更新。')
  const failedRound = [...history, failedResource(pending())]
  assert.equal(generationOutcome(failedRound, history.length).succeeded, 0, 'historical success must not clear selected resource types after a failed round')
  assert.doesNotMatch(generationSummary(failedRound, history.length), /已完成/)
})

test('done and transport failure terminate unfinished slots without overwriting received content', () => {
  const history = ready(1)
  const incoming = { ...pending('lab'), status: 'generating', content: '已收到的部分说明', metadata: { generation_progress: { progress: 30 }, references: ['source'] } }
  const resources = finishGeneration([history, incoming, pending()], 1, { message: '生成连接中断，请从历史对话确认已保存的内容。' })
  assert.strictEqual(resources[0], history)
  assert.equal(resources[1].content, '已收到的部分说明')
  assert.equal(resources[1].status, 'error')
  assert.equal(resources[2].status, 'error')
  assert.equal(resources[1].errorMessage, '生成连接中断，请从历史对话确认已保存的内容。')
  assert.equal(resources[1].metadata.generation_progress, undefined)
  assert.deepEqual(resources[1].metadata.references, ['source'])
  assert.equal(incoming.metadata.generation_progress.progress, 30, 'original records are not mutated')
  assert.equal(generationOutcome(resources, 1).unfinished, 0)
})

test('server human summary is preferred, while empty and stale error titles remain safe', () => {
  assert.equal(generationSummary([], 0, { summary: '这次制作未成功，请缩小问题后重试。' }), '这次制作未成功，请缩小问题后重试。')
  assert.doesNotMatch(generationSummary([], 0), /已完成/)
  assert.equal(resourceLifecycleTitle({ ...pending(), status: 'error' }), '互动教学生成未完成')
  const error = failedResource(pending(), { title: '互动教学生成未完成', message: '请重试。' })
  assert.equal(resourceLifecycleTitle(error), '互动教学生成未完成')
  assert.equal(resourceLifecycleTitle(ready(1)), '')
})
