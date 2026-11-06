export const PDF_PADDING = 16;
const LABEL_HEIGHT = 24;
const PAGE_GAP = 20;

export function buildPdfLayout(pages, availableWidth, zoom) {
  let top = 0;
  const items = pages.map(page => {
    const scale = Math.min(1, availableWidth / page.width) * zoom;
    const height = page.height * scale;
    const item = { number: page.page_number, top, pageTop: top + LABEL_HEIGHT,
      width: page.width * scale, height, scale, bottom: top + LABEL_HEIGHT + height };
    top = item.bottom + PAGE_GAP;
    return item;
  });
  return { items, height: Math.max(0, top - PAGE_GAP), width: Math.max(availableWidth, ...items.map(item => item.width)) };
}

export function pageAtOffset(items, offset) {
  let low = 0;
  let high = items.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (items[middle].bottom < offset) low = middle + 1;
    else high = middle;
  }
  return low;
}

export function visiblePdfPages(items, top, height) {
  if (!items.length) return new Set();
  const first = Math.max(0, pageAtOffset(items, top) - 1);
  const last = Math.min(items.length - 1, pageAtOffset(items, top + height) + 1);
  return new Set(items.slice(first, last + 1).map(item => item.number));
}

export function remapPdfScroll(oldItems, newItems, top) {
  if (!oldItems.length || !newItems.length) return 0;
  const index = pageAtOffset(oldItems, top);
  const old = oldItems[index];
  const next = newItems[index] || newItems.at(-1);
  const fraction = Math.max(0, Math.min(1, (top - old.top) / (old.bottom - old.top)));
  return next.top + fraction * (next.bottom - next.top);
}
