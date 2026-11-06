import test from 'node:test';
import assert from 'node:assert/strict';
import { TOUR_STORAGE_KEY, hasSeenTour, rememberTour, tourGeometry } from './onboarding.mjs';

function checkPlacement(rect, viewport, size) {
  const result = tourGeometry(rect, viewport, size);
  const { card, hole } = result;
  assert.ok(card.left >= 12 && card.top >= 12, 'card preserves viewport margin');
  assert.ok(card.left + card.width <= viewport.width - 12, 'card fits horizontally');
  assert.ok(card.top + size.height <= viewport.height - 12, 'card fits vertically');
  assert.ok(hole.left >= 0 && hole.top >= 0 && hole.right <= viewport.width && hole.bottom <= viewport.height, 'hole is clipped to viewport');
  assert.ok(card.left >= hole.right || card.left + card.width <= hole.left || card.top >= hole.bottom || card.top + size.height <= hole.top, 'card does not cover interactive target when space exists');
  return result;
}

test('desktop card clears a left composer and a right workspace target', () => {
  const viewport = { width: 1440, height: 900 };
  const size = { width: 340, height: 310 };
  checkPlacement({ left: 20, top: 500, right: 400, bottom: 850 }, viewport, size);
  checkPlacement({ left: 1000, top: 50, right: 1400, bottom: 200 }, viewport, size);
});

test('narrow viewport moves card below target and keeps usable margins', () => {
  const { card } = checkPlacement({ left: 10, top: 30, right: 380, bottom: 100 }, { width: 390, height: 844 }, { width: 340, height: 310 });
  assert.equal(card.width, 340);
  assert.ok(card.top >= 105);
});

test('scrolled target clips hole at viewport boundary without covering visible target', () => {
  const { hole } = checkPlacement({ left: -50, top: -90, right: 300, bottom: 120 }, { width: 1200, height: 800 }, { width: 340, height: 310 });
  assert.deepEqual(hole, { left: 0, top: 0, right: 305, bottom: 125 });
});

test('small viewport shrinks card width and absent anchor centers the card', () => {
  const { card, hole } = tourGeometry(null, { width: 280, height: 600 }, { width: 340, height: 310 });
  assert.equal(hole, null);
  assert.deepEqual(card, { left: 12, top: 145, width: 256 });
});

test('skipped and completed outcomes suppress subsequent first-run welcome', () => {
  for (const outcome of ['skipped', 'completed']) {
    const values = new Map();
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
    assert.equal(hasSeenTour(storage), false);
    rememberTour(storage, outcome);
    assert.equal(values.get(TOUR_STORAGE_KEY), outcome);
    assert.equal(hasSeenTour(storage), true);
  }
});

test('denied storage reads and writes do not crash onboarding', () => {
  const storage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(hasSeenTour(storage), false);
  assert.doesNotThrow(() => rememberTour(storage, 'skipped'));
  assert.doesNotThrow(() => rememberTour(storage, 'completed'));
});
