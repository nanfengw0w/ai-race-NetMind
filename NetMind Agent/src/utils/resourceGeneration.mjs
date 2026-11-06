import { FILTER_OPTIONS } from '../constants/resources.js'

const TYPE_LABELS = Object.fromEntries(FILTER_OPTIONS.map(item => [item.key, item.label]))
const UNFINISHED = new Set(['pending', 'generating'])

function text(value) { return typeof value === 'string' ? value.trim() : '' }
function roundOffset(value) { return Number.isInteger(value) && value >= 0 ? value : 0 }
function resourceLabel(resource) { return TYPE_LABELS[resource?.type] || '学习资源' }

/** Status labels must not be inferred from a stale placeholder's generated title. */
export function resourceLifecycleTitle(resource) {
  if (!resource) return ''
  const label = resourceLabel(resource)
  if (resource.status === 'pending') return `${label}等待生成`
  if (resource.status === 'generating') return `${label}生成中`
  if (resource.status === 'error') {
    const title = text(resource.title)
    return title && !/(?:生成中|排队|等待生成)/.test(title) ? title : `${label}生成未完成`
  }
  return ''
}

/** Keep received content for recovery, but never present it as a completed artifact. */
export function failedResource(resource, failure = {}) {
  const metadata = { ...(resource?.metadata || {}) }
  delete metadata.generation_progress
  return {
    ...resource,
    title: text(failure.title) || `${resourceLabel(resource)}生成未完成`,
    content: resource?.content || '',
    errorMessage: text(failure.message) || '这个资源未能完成，请重新发送问题重试。',
    errorDetail: text(failure.detail),
    metadata,
    status: 'error',
    isPlaceholder: true,
  }
}

/** A done event or broken stream must not leave this round's slots spinning. */
export function finishGeneration(resources, generationOffset, failure = {}) {
  const offset = roundOffset(generationOffset)
  return resources.map((resource, index) => (
    index >= offset && UNFINISHED.has(resource?.status)
      ? failedResource(resource, failure)
      : resource
  ))
}

export function generationOutcome(resources, generationOffset) {
  const current = resources.slice(roundOffset(generationOffset)).filter(Boolean)
  return {
    succeeded: current.filter(resource => resource.status === 'ready').length,
    failed: current.filter(resource => resource.status === 'error').length,
    unfinished: current.filter(resource => UNFINISHED.has(resource.status)).length,
    planned: current.length,
  }
}

export function generationSummary(resources, generationOffset, payload = {}) {
  const serverSummary = text(payload.summary)
  if (serverSummary) return serverSummary
  const { succeeded, failed } = generationOutcome(resources, generationOffset)
  if (succeeded === 0) return failed > 0
    ? '本次未能生成可用的学习资源。请查看右侧失败说明，重新发送问题重试。'
    : '本次没有生成学习资源。请调整要求后重新发送问题。'
  if (failed > 0) return `已完成 ${succeeded} 个学习资源，另有 ${failed} 个未能完成。已生成的内容已保留，请查看右侧说明。`
  return `已完成 ${succeeded} 个学习资源，右侧工作台已更新。`
}
