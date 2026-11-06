export function documentKindLabel(reference) {
  const kind = typeof reference === 'string' ? reference : reference?.kind;
  return ({ pdf: 'PDF', docx: 'Word', xlsx: 'Excel', xls: 'Excel', csv: 'CSV', md: 'Markdown', markdown: 'Markdown', txt: '文本' })[kind] || '文档';
}
export function documentLocationLabel(reference) {
  if (reference?.location_label || reference?.label) return reference.location_label || reference.label;
  return reference?.kind && reference.kind !== 'pdf' ? `第 ${reference.page_number || 1} 部分` : `第 ${reference?.page_number || 1} 页`;
}

// Preview label only; the server re-derives and verifies the canonical location.
export function documentSelectionLabel(page, reference) {
  const cells = (page.blocks || []).flatMap(block => block.type === 'table' ? (block.rows || []).flat() : [])
    .filter(cell => cell.address && cell.start < reference.end && cell.end > reference.start);
  if (!cells.length) return page.label;
  const first = cells[0].address;
  const last = cells[cells.length - 1].address;
  return `${page.label} · ${first === last ? first : `${first}–${last}`}`;
}
