import { documentLocationLabel } from './documentReference.mjs';
// Build citation controls from validated server metadata, never from model HTML.
export function mountPdfCitations(root, citations, className) {
  const sources = new Map((citations || []).map(source => [source.id, source]))
  if (!sources.size) return
  const doc = root.ownerDocument
  const walker = doc.createTreeWalker(root, 4)
  const nodes = []
  while (walker.nextNode()) nodes.push(walker.currentNode)
  for (const node of nodes) {
    if (node.parentElement.closest('code,pre,a,button')) continue
    const matches = [...node.textContent.matchAll(/\[\[(S\d+)\]\]/g)]
    if (!matches.some(match => sources.has(match[1]))) continue
    const fragment = doc.createDocumentFragment()
    let offset = 0
    for (const match of matches) {
      fragment.append(doc.createTextNode(node.textContent.slice(offset, match.index)))
      const source = sources.get(match[1])
      if (source) {
        const button = doc.createElement('button')
        button.type = 'button'
        button.dataset.pdfCitation = source.id
        button.className = className
        const label = ['xlsx', 'xls', 'csv'].includes(source.kind) ? '单元格出处' : source.kind && source.kind !== 'pdf' ? '原文出处' : documentLocationLabel(source)
        button.textContent = `${label} ↗`
        button.title = `${source.title} · ${documentLocationLabel(source)}\n${source.text}`
        button.setAttribute('aria-label', `查看出处：${source.title}，${documentLocationLabel(source)}`)
        fragment.append(button)
      } else fragment.append(doc.createTextNode(match[0]))
      offset = match.index + match[0].length
    }
    fragment.append(doc.createTextNode(node.textContent.slice(offset)))
    node.replaceWith(fragment)
  }
}
