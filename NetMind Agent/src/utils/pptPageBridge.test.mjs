import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { PPT_PAGE_STATE, PPT_PAGE_GOTO, isPptPageMessage, installPptInnerBridge, installPptContainerBridge, pptBridgeConfig } from './pptPageBridge.mjs'
import { renderHtmlDeck } from './htmlDeck.js'

function harness() {
  const listeners = new Map(), messages = [], timers = new Map()
  let serial = 0
  const window = {
    parent: { postMessage: data => messages.push(data) },
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn) },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn) },
    dispatchEvent(event) { for (const fn of listeners.get(event.type) || []) fn(event) },
    setTimeout(fn) { timers.set(++serial, fn); return serial }, clearTimeout(id) { timers.delete(id) },
  }
  const document = { readyState: 'complete', documentElement: {}, addEventListener: window.addEventListener, removeEventListener: window.removeEventListener }
  const context = vm.createContext({ window, document, Date, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail } } })
  return { window, document, messages, timers, context, send(source, data) { window.dispatchEvent({ type: 'message', source, data }) }, run(fn) { vm.runInContext(`(${fn.toString()})(${JSON.stringify(pptBridgeConfig({ channel: 'test', initialPage: 2 }))})`, context) } }
}
const state = (page = 2) => ({ type: PPT_PAGE_STATE, channel: 'test', page, total: 3 })
const goto = (page = 2) => ({ type: PPT_PAGE_GOTO, channel: 'test', page })

test('page validators reject extra fields, channels and invalid page numbers', () => {
  assert.equal(isPptPageMessage(state(), 'test'), true)
  for (const data of [{ ...state(), html: 'x' }, { ...state(), page: 0 }, { ...state(), page: 4 }, { ...state(), channel: 'old' }, []]) assert.equal(isPptPageMessage(data, 'test'), false)
  assert.equal(isPptPageMessage(goto(), 'test', PPT_PAGE_GOTO), true)
})

test('trusted container verifies both directions and reconstructs only page payloads', () => {
  const h = harness(), childMessages = [], child = { postMessage: data => childMessages.push(data) }
  h.document.getElementById = () => ({ contentWindow: child })
  h.run(installPptContainerBridge)
  h.send({}, state()); h.send(h.window.parent, state()); h.send(child, { ...state(), html: 'bad' }); h.send(child, { ...state(), channel: 'old' })
  assert.equal(h.messages.length, 0)
  h.send(child, state()); h.send(child, state())
  assert.equal(h.messages.length, 1)
  h.send(child, goto()); h.send({}, goto()); assert.equal(childMessages.length, 0)
  h.send(h.window.parent, goto()); assert.equal(childMessages.length, 1)
  h.send(child, state()); assert.equal(h.messages.length, 2, 'same-page goto handshake is forwarded again')
  h.window.dispatchEvent({ type: 'pagehide' }); h.send(child, state(3)); assert.equal(h.messages.length, 2)
})

test('legacy initialization navigates before reporting and stale delayed events read current dots', () => {
  const h = harness(); let active = 0, locked = true
  let mutation, disconnected = false
  h.context.MutationObserver = class { constructor(fn) { mutation = fn } observe() {} disconnect() { disconnected = true } }
  const pages = [{}, {}, {}]
  const dots = pages.map((_, index) => ({ getAttribute(name) { return name === 'data-i' ? String(index) : index === active ? 'page' : null }, click() { if (!locked) active = index } }))
  h.document.querySelectorAll = selector => selector === '#nav button[data-i]' ? dots : pages
  h.run(installPptInnerBridge)
  assert.equal(h.messages.length, 0, 'locked initialization must not claim target')
  locked = false
  const retry = [...h.timers.values()][0]; h.timers.clear(); retry()
  assert.equal(h.messages[0].page, 2)
  active = 2; mutation()
  assert.equal(h.messages.at(-1).page, 3, 'aria-current observation reports immediately before delayed event')
  h.window.dispatchEvent({ type: 'ppt-slide-change', detail: { index: 0 } })
  assert.equal(h.messages.at(-1).page, 3)
  h.send({}, goto(1)); h.send(h.window.parent, { ...goto(1), channel: 'old' }); assert.equal(active, 2)
  h.send(h.window.parent, goto(1)); assert.equal(active, 0)
  const count = h.messages.length
  h.send(h.window.parent, goto(1)); assert.equal(h.messages.length, count + 1, 'same-page goto acknowledges actual state')
  locked = true; h.send(h.window.parent, goto(3))
  h.window.dispatchEvent({ type: 'pointerdown', isTrusted: true })
  locked = false
  for (const callback of [...h.timers.values()]) callback()
  assert.equal(active, 0, 'trusted input cancels locked retry')
  h.window.dispatchEvent({ type: 'pagehide' }); h.send(h.window.parent, goto(3)); assert.equal(active, 0)
  assert.equal(disconnected, true)
})

test('native overview thumbnail clones never change the canonical page count', () => {
  const h = harness(); let active = 0, overview = false
  const pages = [{}, {}, {}]
  const dots = pages.map((_, index) => ({ getAttribute(name) { return name === 'data-i' ? String(index) : index === active ? 'page' : null }, click() { active = index } }))
  h.document.querySelectorAll = selector => {
    if (selector === '#nav button[data-i]') return dots
    if (selector === '#deck > section.slide') return pages
    return overview ? [...pages, ...pages] : pages
  }
  h.run(installPptInnerBridge)
  assert.equal(h.messages.at(-1).total, 3)
  overview = true
  h.send(h.window.parent, goto(3))
  assert.equal(h.messages.at(-1).total, 3)
  assert.equal(h.messages.at(-1).page, 3)
})

test('page bridge is optional and channel cannot escape a serialized script', () => {
  const source = '<html><head></head><body><section class="slide">x</section></body></html>'
  assert.doesNotMatch(renderHtmlDeck(source), /installPptInnerBridge|installPptContainerBridge/)
  const rendered = renderHtmlDeck(source, { pageSync: { channel: '</script><img src=x>', initialPage: 2 } })
  assert.match(rendered, /installPptInnerBridge/)
  assert.match(rendered, /installPptContainerBridge/)
  assert.doesNotMatch(rendered, /allow-same-origin|<img src=x>/)
  assert.match(rendered, /\\u003c\/script>/)
})

test('fallback pager hook initializes, clamps, and reports actual page', () => {
  const h = harness(); let active = 0
  const pages = [0, 1, 2].map(index => ({ getAttribute: () => active === index ? 'active' : 'inactive' }))
  h.document.querySelectorAll = selector => selector === 'section.slide' ? pages : []
  h.window.addEventListener('netmind:slidegoto', event => { active = event.detail.page - 1; h.window.dispatchEvent({ type: 'netmind:slidechange' }) })
  h.run(installPptInnerBridge)
  assert.equal(h.messages[0].page, 2)
  h.send(h.window.parent, goto(99)); assert.equal(h.messages.at(-1).page, 3)
})
