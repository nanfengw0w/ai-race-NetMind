const API_BASE = import.meta.env.VITE_API_BASE || ''

export async function understandingCheckRequest(checkId, identity, action = '', body = {}) {
  const query = new URLSearchParams({ student_id: identity.studentId, user_id: identity.userId })
  const response = await fetch(`${API_BASE}/api/understanding-checks/${encodeURIComponent(checkId)}${action ? `/${action}` : ''}?${query}`, action ? {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ student_id: identity.studentId, user_id: identity.userId, ...body }),
  } : undefined)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(typeof payload.detail === 'string' ? payload.detail : `请求未完成（${response.status}），请重试。`)
    error.status = response.status
    throw error
  }
  if (!payload.check) throw new Error('未收到完整的检查记录，请重新载入。')
  return payload.check
}
