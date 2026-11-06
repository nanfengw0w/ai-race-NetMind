const RESOURCE_AGENTS = new Set([
  'lecture',
  'interactive',
  'quiz',
  'mindmap',
  'ppt',
  'lab',
  'path',
  'assessment',
])

function isResourceStep(step) {
  return step?.kind === 'resource'
    || Number.isInteger(step?.index)
    || RESOURCE_AGENTS.has(step?.agent)
}

function stepKey(item, kind = null) {
  if (Number.isInteger(item?.index)) return `resource-${item.index}`
  return `${kind || item?.kind || 'pipeline'}-${item?.agent || 'unknown'}`
}

function createStep(item, status, kind) {
  return {
    key: stepKey(item, kind),
    agent: item?.agent || item?.intent || 'unknown',
    index: Number.isInteger(item?.index) ? item.index : undefined,
    kind,
    label: item?.label || item?.agent || item?.intent || 'Processing',
    message: item?.message || item?.label || 'Processing',
    status,
  }
}

export function applyStatusEvent(steps, incoming, defaults = []) {
  const source = steps.length > 0 ? steps : defaults
  const next = source.map(step => ({
      ...step,
      key: step.key || stepKey(step, 'pipeline'),
      kind: isResourceStep(step) ? 'resource' : 'pipeline',
    }))
  const resourceEvent = incoming?.phase === 'resource'
    || Number.isInteger(incoming?.index)
    || RESOURCE_AGENTS.has(incoming?.agent)

  if (resourceEvent) {
    return updateResourceProgress(next, incoming, incoming?.status || 'current')
  }

  const key = stepKey(incoming, 'pipeline')
  const current = createStep(incoming, incoming?.status || 'current', 'pipeline')
  const existingIndex = next.findIndex(step => step.key === key)
  const updated = next.map((step, index) => {
    if (index === existingIndex) return { ...step, ...current }
    if (step.kind === 'pipeline' && step.status === 'current') {
      return { ...step, status: 'done' }
    }
    return step
  })

  if (existingIndex < 0) updated.push(current)
  return updated
}

export function syncPlannedProgress(steps, resources = []) {
  const pipelineSteps = steps
    .filter(step => !isResourceStep(step))
    .map(step => ({
      ...step,
      key: step.key || stepKey(step, 'pipeline'),
      kind: 'pipeline',
    }))
  const resourceSteps = resources.map(item => createStep({
    ...item,
    agent: item.intent,
    message: item.message || `${item.label || item.intent || 'Resource'} queued`,
  }, 'pending', 'resource'))

  return [...pipelineSteps, ...resourceSteps]
}

export function updateResourceProgress(steps, payload, status, message = null) {
  const next = steps.map(step => ({ ...step }))
  const key = stepKey(payload, 'resource')
  let existingIndex = next.findIndex(step => step.key === key)

  if (existingIndex < 0 && !Number.isInteger(payload?.index)) {
    existingIndex = next.findIndex(step => (
      step.kind === 'resource'
      && step.agent === (payload?.agent || payload?.intent)
      && step.status !== 'done'
      && step.status !== 'error'
    ))
  }

  const existing = existingIndex >= 0 ? next[existingIndex] : null
  const incoming = {
    ...existing,
    ...payload,
    agent: payload?.agent || payload?.intent,
    label: payload?.label || existing?.label,
    message: message || payload?.message || existing?.message,
  }
  const updated = createStep(incoming, status, 'resource')

  if (existingIndex >= 0) {
    next[existingIndex] = {
      ...next[existingIndex],
      ...updated,
      key: next[existingIndex].key,
    }
  } else {
    next.push(updated)
  }

  if (status === 'current') {
    return next.map(step => (
      step.kind === 'pipeline' && step.status === 'current'
        ? { ...step, status: 'done' }
        : step
    ))
  }
  return next
}

export function finalizeProgress(steps) {
  return steps.map(step => {
    if (step.kind === 'pipeline' && step.status === 'current') {
      return { ...step, status: 'done' }
    }
    return step
  })
}
