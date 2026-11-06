import test from 'node:test';
import assert from 'node:assert/strict';
import { buildPdfLayout, pageAtOffset, visiblePdfPages, remapPdfScroll } from './pdfLayout.mjs';

test('mixed page sizes keep stable positions, and scrolling mounts only nearby pages', () => {
  const pages = Array.from({ length: 500 }, (_, i) => ({ page_number: i + 1, width: i % 2 ? 1200 : 600, height: i % 2 ? 600 : 900 }));
  const layout = buildPdfLayout(pages, 600, 1);
  assert.equal(layout.items[0].height, 900);
  assert.equal(layout.items[1].height, 300);
  assert.equal(layout.items[1].top, 944);
  const target = layout.items[349];
  assert.equal(pageAtOffset(layout.items, target.pageTop + 20), 349);
  const mounted = visiblePdfPages(layout.items, target.top, 600);
  assert.ok(mounted.has(350));
  assert.ok(mounted.size < 7);
  assert.ok(!mounted.has(1));
  assert.equal(pageAtOffset(layout.items, -50), 0);
  assert.equal(pageAtOffset(layout.items, layout.height + 500), 499);
});

test('zoom and viewport resize preserve the reading position within the same page', () => {
  const pages = Array.from({ length: 20 }, (_, i) => ({ page_number: i + 1, width: 600, height: 900 }));
  const before = buildPdfLayout(pages, 600, 1);
  const after = buildPdfLayout(pages, 400, 1.25);
  const old = before.items[14], next = after.items[14];
  const top = remapPdfScroll(before.items, after.items, old.top + (old.bottom - old.top) * .6);
  assert.equal(pageAtOffset(after.items, top), 14);
  assert.ok(Math.abs((top - next.top) / (next.bottom - next.top) - .6) < 1e-9);
});
