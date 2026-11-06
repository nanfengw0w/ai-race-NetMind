import test from 'node:test'
import assert from 'node:assert/strict'
import { CHAT_WIDTH_KEYS, getChatLayout, parseChatWidth } from './chatLayout.js'

test('laptops start compact even when the old desktop preference is very wide', () => {
  const preferences = { desktop: 680, compact: null }
  assert.equal(getChatLayout(1366, 1366, preferences).width, 300)
  assert.equal(getChatLayout(1707, 1707, preferences).width, 300)
  assert.equal(getChatLayout(1024, 1024, preferences).width, 280)
  assert.equal(getChatLayout(1920, 1920, preferences).width, 680)
  assert.deepEqual(preferences, { desktop: 680, compact: null })
})

test('a restored compact preference adapts to room and returns when room becomes available', () => {
  const preferences = { desktop: 620, compact: 500 }
  const narrow = getChatLayout(1024, 1024, preferences)
  assert.equal(narrow.width, 455)
  assert.equal(1024 - narrow.width - 9, 560)
  assert.equal(getChatLayout(1366, 1366, preferences).width, 500)
  assert.equal(preferences.compact, 500)
})

test('desktop and laptop drag preferences remain independent across breakpoints', () => {
  const preferences = { desktop: 600, compact: 330 }
  const compact = getChatLayout(1800, 1800, preferences)
  const desktop = getChatLayout(1801, 1801, preferences)
  assert.equal(compact.width, 330)
  assert.equal(compact.preferenceKey, 'compact')
  assert.equal(desktop.width, 600)
  assert.equal(desktop.preferenceKey, 'desktop')
  assert.notEqual(CHAT_WIDTH_KEYS[compact.preferenceKey], CHAT_WIDTH_KEYS[desktop.preferenceKey])
  const external = getChatLayout(2560, 2560, preferences)
  assert.equal(external.width, 600)
  assert.equal(external.preferenceKey, 'desktop')
})

test('remaining main space after an open history sidebar also bounds dragging', () => {
  const layout = getChatLayout(1100, 840, { compact: 500 })
  assert.equal(layout.minWidth, 280)
  assert.equal(layout.maxWidth, 280)
  assert.equal(layout.width, 280)
})

test('phone breakpoint stays vertical and invalid saved values use sensible defaults', () => {
  assert.equal(getChatLayout(760, 760).mobile, true)
  assert.equal(getChatLayout(761, 761).mobile, false)
  for (const value of [null, undefined, '', ' ', 'NaN', 'Infinity', '-1', '0']) {
    assert.equal(parseChatWidth(value), null)
  }
  assert.equal(parseChatWidth('420'), 420)
  assert.equal(getChatLayout(1920, 1920, { desktop: 'bad' }).width, 420)
  assert.equal(getChatLayout(1920, 1920, { desktop: 9999 }).width, 680)
})
