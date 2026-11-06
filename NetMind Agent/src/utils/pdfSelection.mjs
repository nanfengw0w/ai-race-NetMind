export const codePointLength = text => Array.from(text).length;
export const codePointSlice = (text, start, end) => Array.from(text).slice(start, end).join('');

// DOM Range offsets count UTF-16 units; persisted PDF offsets count Unicode code points.
export function endpointOffset(root, node, offset) {
  const element = node.nodeType === 1 ? node : node.parentElement;
  const span = element?.closest('[data-pdf-start]');
  if (!span || !root.contains(span)) return null;
  const prefix = root.ownerDocument.createRange();
  prefix.selectNodeContents(span);
  try { prefix.setEnd(node, offset); } catch { return null; }
  return Number(span.dataset.pdfStart) + codePointLength(prefix.toString());
}

export function selectionReference(root, selection, page, document) {
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
  const range = selection.getRangeAt(0);
  const start = endpointOffset(root, range.startContainer, range.startOffset);
  const end = endpointOffset(root, range.endContainer, range.endOffset);
  if (start === null || end === null || start >= end || end > codePointLength(page.text)) return null;
  const text = codePointSlice(page.text, start, end);
  if (!text.trim()) return null;
  return { title: document.title || document.name || 'PDF 原文', document_id: document.id, content_hash: page.content_hash || document.content_hash, page_number: page.page_number, start, end, text };
}

export function spanHighlight(span, location) {
  if (!location || location.start >= span.end || location.end <= span.start) return null;
  const start = Math.max(0, location.start - span.start);
  const end = Math.min(codePointLength(span.text), location.end - span.start);
  return { before: codePointSlice(span.text, 0, start), selected: codePointSlice(span.text, start, end), after: codePointSlice(span.text, end) };
}

export function pdfLocationError(page, location) {
  if (!page || !location || location.page_number !== page.page_number) return '';
  if (location.content_hash && location.content_hash !== page.content_hash) return '此出处与当前 PDF 版本不匹配，无法高亮。';
  if (!Number.isInteger(location.start) || !Number.isInteger(location.end) || location.start < 0 || location.start >= location.end || location.end > codePointLength(page.text)) return '此出处的原文范围无效，无法高亮。';
  if (location.text && codePointSlice(page.text, location.start, location.end) !== location.text) return '此出处文字与当前原页不匹配，无法高亮。';
  return '';
}
