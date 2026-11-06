import { marked } from 'marked'

export function artifactVersion(resource) {
  const version = Number(resource?.version || resource?.current_version || 1)
  return Number.isInteger(version) && version > 0 ? version : 1
}

export function artifactReference(resource) {
  if (resource?.id === undefined || resource?.id === null || resource.isPlaceholder
    || ['pending', 'generating', 'error'].includes(resource.status)) return null
  return { resource_id: resource.id, version: artifactVersion(resource), ...(Number.isInteger(Number(resource.current_version)) && Number(resource.current_version) > 0 ? { current_version: Number(resource.current_version) } : {}), title: resource.title || '未命名作品' }
}

export function canReviseArtifact(type) {
  return ['document', 'lecture', 'interactive', 'ppt', 'lab', 'assessment'].includes(type)
}

export function artifactDownload(resource) {
  const html = ['interactive', 'ppt'].includes(resource?.type)
  const structured = ['quiz', 'mindmap', 'learning_path'].includes(resource?.type)
  const extension = html ? 'html' : structured ? 'json' : 'md'
  const title = String(resource?.title || '未命名作品').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/[.\s]+$/g, '').slice(0, 100) || '作品'
  const text = typeof resource?.content === 'string' ? resource.content : JSON.stringify(resource?.content ?? '', null, 2)
  return {
    filename: `${title}-v${artifactVersion(resource)}.${extension}`,
    text,
    // HTML is offered as a downloaded attachment, never opened or executed here.
    mime: html ? 'application/octet-stream' : structured ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8',
  }
}

// A new version keeps the artifact ID. Replace the existing card in place.
export function upsertArtifacts(current = [], incoming = []) {
  const result = [...current]
  for (const resource of incoming) {
    const index = result.findIndex(item => item?.id != null && resource?.id != null && String(item.id) === String(resource.id))
    if (index < 0) result.unshift(resource)
    else if (artifactVersion(resource) >= artifactVersion(result[index])) result[index] = resource
  }
  return result
}

// Each stream may confirm the same committed version through artifact and done.
export function artifactEventReceiver(onSaved, onAction) {
  const applied = new Set()
  return artifact => {
    if (artifact?.resource?.id == null || !['revise', 'restore'].includes(artifact.action)) return
    const key = `${artifact.resource.id}:${artifactVersion(artifact.resource)}`
    onAction(artifact.action)
    if (applied.has(key)) return
    applied.add(key)
    onSaved(artifact.resource)
  }
}

// Offsets sent to the server count Unicode code points, while marked uses JS strings.
export function markdownParagraphs(resource) {
  const content = resource?.content
  if (!artifactReference(resource) || !['document', 'lecture', 'lab', 'assessment'].includes(resource.type)
    || typeof content !== 'string' || /^\s*</.test(content)) return null
  try {
    const parsed = JSON.parse(content)
    if (parsed !== null && typeof parsed === 'object') return null
  } catch { /* Markdown is normally not JSON. */ }
  // Only the lexer input folds line endings. Every normalized UTF-16 boundary
  // maps back to an original UTF-16 boundary, including the full CRLF pair.
  const normalizedParts = []
  const originalBoundaries = [0]
  for (let index = 0; index < content.length;) {
    const character = content[index]
    if (character === '\r') {
      index += content[index + 1] === '\n' ? 2 : 1
      normalizedParts.push('\n')
    } else {
      index += 1
      normalizedParts.push(character)
    }
    originalBoundaries.push(index)
  }
  const normalized = normalizedParts.join('')
  const paragraphs = []
  let cursor = 0
  try {
    for (const token of marked.lexer(normalized, { gfm: true, breaks: true })) {
      const raw = token.raw
      if (typeof raw !== 'string' || normalized.slice(cursor, cursor + raw.length) !== raw) {
        return { paragraphs: [], error: '原文格式无法精确定位，暂不支持选择段落。' }
      }
      if (token.type === 'paragraph') {
        const selectedLength = raw.replace(/\n+$/, '').length
        const originalStart = originalBoundaries[cursor]
        const originalEnd = originalBoundaries[cursor + selectedLength]
        if (selectedLength) paragraphs.push({
          start: Array.from(content.slice(0, originalStart)).length,
          end: Array.from(content.slice(0, originalEnd)).length,
          text: content.slice(originalStart, originalEnd),
        })
      }
      cursor += raw.length
    }
    if (cursor !== normalized.length) return { paragraphs: [], error: '原文格式无法精确定位，暂不支持选择段落。' }
    return { paragraphs, error: '' }
  } catch {
    return { paragraphs: [], error: '原文格式无法精确定位，暂不支持选择段落。' }
  }
}

export function artifactSelection(resource, reference) {
  if (!reference || String(reference.resource_id) !== String(resource?.id)
    || reference.version !== artifactVersion(resource)) return null
  const selection = reference.selection
  if (!selection) return null
  if (selection.slide_number !== undefined) {
    if (resource?.type !== 'ppt' || !artifactReference(resource)
      || !Number.isInteger(selection.slide_number) || selection.slide_number < 1
      || !Number.isInteger(selection.start) || !Number.isInteger(selection.end)
      || selection.start < 0 || selection.end <= selection.start
      || typeof resource.content !== 'string' || typeof selection.text !== 'string') return null
    const points = Array.from(resource.content)
    return selection.end <= points.length && points.slice(selection.start, selection.end).join('') === selection.text
      ? selection : null
  }
  return markdownParagraphs(resource)?.paragraphs.find(item => item.start === selection.start
    && item.end === selection.end && item.text === selection.text) || null
}

export function updatedArtifactReference(current, saved) {
  if (!current || String(current.resource_id) !== String(saved.id)) return current
  const { selection: _selection, ...reference } = current
  void _selection
  return { ...reference, ...artifactReference(saved) }
}

export function artifactSelectionLabel(selection) {
  if (!selection) return ''
  if (Number.isInteger(selection.slide_number) && selection.slide_number > 0) return `第${selection.slide_number}页`
  const points = Array.from(selection.text || '')
  return `所选段落：${points.slice(0, 100).join('')}${points.length > 100 ? '…' : ''}`
}
