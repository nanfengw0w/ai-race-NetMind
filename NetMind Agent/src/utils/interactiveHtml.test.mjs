import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { buildInteractiveContainer, escapeInteractiveAttribute, getInteractiveTitle, interactiveFilename, INTERACTIVE_CONTAINER_CSP, INTERACTIVE_ESCAPE_MESSAGE, INTERACTIVE_INNER_CSP, INTERACTIVE_MAX_HEIGHT, INTERACTIVE_MIN_HEIGHT, INTERACTIVE_RESIZE_MESSAGE, INTERACTIVE_SANDBOX, isInteractiveEscapeMessage, isInteractiveResizeMessage, prepareInteractiveHtml, validateInteractiveHtml } from './interactiveHtml.mjs'

const original = `<!doctype html><html><head><style>output{color:red}</style></head><body><input id="value" type="range"><output id="result">0</output><script>document.getElementById('value').oninput = e => { document.getElementById('result').textContent = e.target.value * 2; };</script></body></html>`

test('interactive titles remove design annotations but retain real subject terms', () => {
  for (const title of ['水的三相变化 · 瑞士国际主义', '后现代主义设计审美 | 水的三相变化', '水的三相变化（简洁美观）', '【构成主义简洁设计审美】水的三相变化', '后现代主义设计审美 / 水的三相变化 · 瑞士国际主义']) {
    assert.equal(getInteractiveTitle(title), '水的三相变化')
  }
  for (const title of ['瑞士国际主义的历史', '后现代主义设计审美与现代主义比较', '瑞士国际主义', 'IPv4/IPv6：地址格式']) assert.equal(getInteractiveTitle(title), title)
  assert.equal(getInteractiveTitle(''), '互动教学')
  assert.equal(getInteractiveTitle(undefined, ''), '')
})

function unescapeAttribute(value) {
  return value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&')
}

test('complete local interactions survive wrapping and trusted CSP precedes model markup', () => {
  const { preview, error } = prepareInteractiveHtml(original, '交互实验')
  assert.equal(error, '')
  assert.equal(INTERACTIVE_SANDBOX, 'allow-scripts')
  assert.match(preview, /sandbox="allow-scripts"/)
  assert.doesNotMatch(preview, /sandbox="[^"]*(?:allow-same-origin|allow-popups|allow-forms|allow-top-navigation)/)
  const inner = unescapeAttribute(preview.match(/ srcdoc="([^"]*)"/)[1])
  assert.ok(inner.indexOf('Content-Security-Policy') < inner.indexOf("document.getElementById('value')"))
  assert.ok(inner.endsWith(original), 'preserve original JS and CSS without destructive HTML sanitizing')
  assert.ok(INTERACTIVE_INNER_CSP.includes("connect-src 'none'"))
  assert.ok(INTERACTIVE_INNER_CSP.includes("frame-src 'none'"))
  assert.ok(INTERACTIVE_CONTAINER_CSP.includes('frame-src about:'))
  assert.ok(!INTERACTIVE_INNER_CSP.includes('unsafe-eval'))
});

test('hostile closing tags, quotes, meta and scripts cannot break the srcdoc attribute into the trusted container', () => {
  const attack = `<!doctype html><html><body><p title='" onload="bad()'>测试</p><script>window.outerAttack = '</iframe><script>window.hostEscape=true;<\/script>';</script><meta http-equiv="Content-Security-Policy" content="default-src *"></body></html>`
  const container = buildInteractiveContainer(attack, { title: '"><script>bad()</script>', standalone: true })
  assert.equal((container.match(/<iframe\b/g) || []).length, 1)
  assert.equal((container.match(/<script>/g) || []).length, 1, 'the only outer script is the trusted restart and Escape bridge')
  assert.ok(container.includes('&lt;/iframe&gt;'))
  assert.equal(escapeInteractiveAttribute('"<&\''), '&quot;&lt;&amp;&#39;')
  assert.match(container, /<title>&quot;&gt;&lt;script&gt;/)
});

test('downloaded document retains the same isolated inner frame and restart restores its original source', () => {
  const prepared = prepareInteractiveHtml(original, '本地实验')
  const previewInner = prepared.preview.match(/ srcdoc="([^"]*)"/)[1]
  const downloadedInner = prepared.standalone.match(/ srcdoc="([^"]*)"/)[1]
  assert.equal(downloadedInner, previewInner)
  assert.match(prepared.standalone, /sandbox="allow-scripts"/)
  let restart
  const frame = { srcdoc: 'changed', getAttribute: name => name === 'srcdoc' ? unescapeAttribute(downloadedInner) : null }
  const button = { addEventListener: (type, listener) => { assert.equal(type, 'click'); restart = listener } }
  const document = { getElementById: id => id === 'netmind-interactive-content' ? frame : button }
  const script = prepared.standalone.match(/<script>([\s\S]*?)<\/script>/)[1]
  vm.runInNewContext(script, { document, window: { addEventListener() {} } })
  assert.equal(frame.srcdoc, 'changed')
  restart()
  assert.ok(frame.srcdoc.endsWith(original))
});

test('Escape bridge forwards only the current inner frame and never forwards to itself in a standalone file', () => {
  const { standalone } = prepareInteractiveHtml(original, '本地实验')
  const script = standalone.match(/<script>([\s\S]*?)<\/script>/)[1]
  const childWindow = {}
  const frame = { contentWindow: childWindow, getAttribute: () => 'original' }
  const document = { getElementById: id => id === 'netmind-interactive-content' ? frame : null }
  let handler
  const forwarded = []
  const window = { parent: { postMessage: data => forwarded.push(data) }, addEventListener: (name, listener) => { if (name === 'message') handler = listener } }
  vm.runInNewContext(script, { document, window })
  handler({ source: {}, data: { type: INTERACTIVE_ESCAPE_MESSAGE } })
  handler({ source: childWindow, data: { type: 'ready' } })
  handler({ source: childWindow, data: { type: INTERACTIVE_ESCAPE_MESSAGE, permission: 'anything' } })
  assert.equal(forwarded.length, 0)
  handler({ source: childWindow, data: { type: INTERACTIVE_ESCAPE_MESSAGE } })
  assert.equal(forwarded.length, 1)
  assert.equal(forwarded[0].type, INTERACTIVE_ESCAPE_MESSAGE)
  window.parent = window
  handler({ source: childWindow, data: { type: INTERACTIVE_ESCAPE_MESSAGE } })
  assert.equal(forwarded.length, 1, 'standalone wrapper must not post to itself')
  assert.equal(isInteractiveEscapeMessage({ type: INTERACTIVE_ESCAPE_MESSAGE }), true)
  assert.equal(isInteractiveEscapeMessage({ type: INTERACTIVE_ESCAPE_MESSAGE, ready: true }), false)
  assert.equal(isInteractiveEscapeMessage(null), false)
});

test('PPT overview owns Escape while ordinary interactive pages and generic decks still close their preview', () => {
  function harness({ preserveDeckEscape = false, navigation = null, ids = [] } = {}) {
    const forwarded = []
    let keydown
    const document = { documentElement: { getAttribute: name => name === 'data-netmind-navigation' ? navigation : null }, getElementById: id => ids.includes(id) ? {} : null }
    const window = { parent: { postMessage: value => forwarded.push(value) }, addEventListener: (name, listener) => { if (name === 'keydown') keydown = listener } }
    const wrapped = buildInteractiveContainer(original, { preserveDeckEscape })
    const inner = unescapeAttribute(wrapped.match(/ srcdoc="([^"]*)"/)[1])
    vm.runInNewContext(inner.match(/<script>([\s\S]*?)<\/script>/)[1], { document, window })
    return { forwarded, press: extra => keydown({ key: 'Escape', ...extra }) }
  }
  for (const options of [
    { preserveDeckEscape: true, navigation: 'legacy-dots', ids: ['deck', 'nav', 'netmind-ppt-navigation'] },
    { preserveDeckEscape: true, ids: ['deck', 'nav', 'overview', 'hint'] },
  ]) {
    const ownNavigation = harness(options)
    ownNavigation.press({ preventDefault: () => assert.fail('bridge must leave PPT overview in control') })
    assert.equal(ownNavigation.forwarded.length, 0)
  }
  for (const options of [
    { preserveDeckEscape: true, navigation: 'legacy-dots', ids: ['deck', 'nav'] },
    { preserveDeckEscape: true, ids: ['netmind-deck-navigation'] },
    { preserveDeckEscape: false, navigation: 'legacy-dots', ids: ['deck', 'nav', 'netmind-ppt-navigation'] },
  ]) {
    const hostNavigation = harness(options)
    hostNavigation.press()
    assert.equal(hostNavigation.forwarded.length, 1)
    assert.equal(hostNavigation.forwarded[0].type, INTERACTIVE_ESCAPE_MESSAGE)
    hostNavigation.press({ defaultPrevented: true })
    hostNavigation.press({ key: 'ArrowRight' })
    assert.equal(hostNavigation.forwarded.length, 1)
  }
});

function eventTarget(target = {}) {
  const handlers = new Map()
  return Object.assign(target, {
    handlers,
    addEventListener(name, listener) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(listener) },
    removeEventListener(name, listener) { handlers.get(name)?.delete(listener) },
    emit(name, event = {}) { for (const listener of [...(handlers.get(name) || [])]) listener(event) },
  })
}

function fakeClock() {
  const tasks = new Map()
  let id = 0
  return {
    tasks,
    setTimeout(callback) { tasks.set(++id, callback); return id },
    clearTimeout(key) { tasks.delete(key) },
    requestAnimationFrame(callback) { tasks.set(++id, callback); return id },
    cancelAnimationFrame(key) { tasks.delete(key) },
    flush() { for (let turns = 0; tasks.size && turns < 30; turns += 1) { const batch = [...tasks.values()]; tasks.clear(); batch.forEach(callback => callback(100)) } assert.equal(tasks.size, 0, 'height observation must settle') },
  }
}

class FakeStyle {
  constructor(properties = {}) { this.values = new Map(Object.entries(properties)); this.priorities = new Map() }
  [Symbol.iterator]() { return this.values.keys() }
  getPropertyValue(name) { return this.values.get(name) || '' }
  getPropertyPriority(name) { return this.priorities.get(name) || '' }
  setProperty(name, value, priority = '') { this.values.set(name, value); this.priorities.set(name, priority) }
}

function innerHarness() {
  const clock = fakeClock(), forwarded = [], observers = []
  const body = { style: new FakeStyle({ 'min-height': '100vh', 'padding-bottom': '40px' }), children: [], rect: { top: 0, bottom: 760, height: 760, width: 800 } }
  const root = { style: new FakeStyle({ height: '100vh' }) }
  Object.defineProperty(root, 'scrollHeight', { get: () => { throw new Error('viewport floor must not be measured') } })
  Object.defineProperty(root, 'clientHeight', { get: () => { throw new Error('viewport floor must not be measured') } })
  body.getBoundingClientRect = () => body.rect
  const viewportStyle = new FakeStyle({ 'min-height': '100vh', height: '50vh' })
  const document = eventTarget({ body, documentElement: root, readyState: 'complete', styleSheets: [{ cssRules: [{ style: viewportStyle }] }], querySelectorAll: () => [root, body] })
  const window = eventTarget({ innerWidth: 800, scrollY: 0, ...clock, parent: { postMessage: data => forwarded.push(JSON.parse(JSON.stringify(data))) }, getComputedStyle: element => ({ display: 'block', position: 'static', marginBottom: element === body ? '12px' : '0px', ...element.computed }) })
  class FakeObserver {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this) }
    observe() {}
    disconnect() { this.disconnected = true }
  }
  const prepared = prepareInteractiveHtml(original, '自适应实验')
  const inner = unescapeAttribute(prepared.preview.match(/ srcdoc="([^"]*)"/)[1])
  const script = inner.match(/<script>([\s\S]*?)<\/script>/)[1]
  vm.runInNewContext(script, { document, window, ResizeObserver: FakeObserver, MutationObserver: FakeObserver })
  return { clock, forwarded, body, root, viewportStyle, document, window, observers }
}

test('actual inner bootstrap measures natural body bounds and can shrink below its previous viewport', () => {
  const harness = innerHarness()
  harness.clock.flush()
  assert.deepEqual(harness.forwarded, [{ type: INTERACTIVE_RESIZE_MESSAGE, height: 774 }])
  assert.equal(harness.body.style.getPropertyValue('padding-bottom'), '40px', 'keep original body padding')
  assert.equal(harness.body.style.getPropertyValue('min-height'), '0px')
  assert.equal(harness.root.style.getPropertyValue('height'), 'auto')
  harness.body.rect = { top: 0, bottom: 180, height: 180, width: 800 }
  harness.observers.forEach(observer => observer.callback())
  harness.clock.flush()
  assert.equal(harness.forwarded.at(-1).height, 194, 'tall-to-short content must shrink even when viewport stayed tall')
  harness.observers.forEach(observer => observer.callback())
  harness.clock.flush()
  assert.equal(harness.forwarded.length, 2, 'unchanged height must not trigger a feedback loop')
});

test('inner observation coalesces mutations, responds to width and image load, and cleans up on unload', () => {
  const harness = innerHarness()
  for (let i = 0; i < 20; i += 1) harness.observers.forEach(observer => observer.callback())
  assert.equal(harness.clock.tasks.size, 1, 'bursty changes share one pending measurement')
  harness.clock.flush()
  const oldViewportUnit = harness.root.style.getPropertyValue('--netmind-embedded-vh')
  harness.window.innerWidth = 500; harness.body.rect.bottom = 1030
  harness.window.emit('resize'); harness.document.emit('load')
  harness.clock.flush()
  assert.equal(harness.forwarded.at(-1).height, 1044)
  assert.notEqual(harness.root.style.getPropertyValue('--netmind-embedded-vh'), oldViewportUnit)
  assert.equal(harness.viewportStyle.getPropertyValue('min-height'), '0px')
  assert.match(harness.viewportStyle.getPropertyValue('height'), /var\(--netmind-embedded-vh\)/)
  harness.window.emit('resize')
  assert.equal(harness.clock.tasks.size, 0, 'height-only viewport resize must not remeasure itself')
  harness.observers[0].callback(); assert.equal(harness.clock.tasks.size, 1)
  harness.window.emit('pagehide')
  assert.equal(harness.clock.tasks.size, 0)
  assert.ok(harness.observers.every(observer => observer.disconnected))
  assert.equal(harness.window.handlers.get('resize').size, 0)
});

test('closed details ignores hidden descendant rects and expands/shrinks without dropping opacity-zero layout', () => {
  const harness = innerHarness()
  const node = (tagName, top, bottom, children = [], extra = {}) => ({ tagName, children, getBoundingClientRect: () => ({ top, bottom, height: bottom - top, width: 500 }), ...extra })
  const summary = node('SUMMARY', 230, 260)
  const hiddenContent = node('DIV', 260, 940)
  const details = node('DETAILS', 230, 260, [summary, hiddenContent], { open: false })
  const opacityPlaceholder = node('DIV', 260, 340, [], { computed: { opacity: '0' } })
  const offscreenHelper = node('SPAN', -20000, -19999)
  const clippedChild = node('DIV', 250, 5000)
  const clippedParent = node('DIV', 250, 280, [clippedChild], { computed: { overflowY: 'hidden' } })
  harness.body.rect = { top: 0, bottom: 310, height: 310, width: 800 }
  harness.body.children = [details, opacityPlaceholder, offscreenHelper, clippedParent, node('DIV', 0, 8000, [], { hidden: true })]
  harness.clock.flush()
  assert.equal(harness.forwarded.at(-1).height, 342, 'closed details must ignore its hidden content, while opacity-zero still occupies space')
  details.open = true; harness.body.rect.bottom = 960
  harness.observers[0].callback(); harness.clock.flush()
  assert.equal(harness.forwarded.at(-1).height, 974)
  details.open = false; harness.body.rect.bottom = 310
  harness.observers[0].callback(); harness.clock.flush()
  assert.equal(harness.forwarded.at(-1).height, 342, 'collapsing must restore the short natural layout')
});

test('very tall content retains a bounded inner scroll fallback instead of hiding the overflow', () => {
  const harness = innerHarness()
  harness.body.rect.bottom = 50000
  harness.clock.flush()
  assert.ok(harness.forwarded.at(-1).height < INTERACTIVE_MAX_HEIGHT)
  assert.equal(harness.root.style.getPropertyValue('overflow-y'), 'auto')
  harness.body.rect.bottom = 120; harness.observers[0].callback(); harness.clock.flush()
  assert.equal(harness.forwarded.at(-1).height, 134)
  assert.equal(harness.root.style.getPropertyValue('overflow-y'), 'hidden')
});

function containerHarness(standalone = false) {
  const clock = fakeClock(), forwarded = [], childWindow = {}
  const frame = { contentWindow: childWindow, style: {}, srcdoc: 'original', getAttribute: () => 'original' }
  const header = { getBoundingClientRect: () => ({ height: 64 }) }
  const restart = eventTarget()
  const document = { body: { style: {} }, documentElement: { style: {} }, getElementById: id => id === 'netmind-interactive-content' ? frame : id === 'netmind-interactive-header' && standalone ? header : id === 'netmind-interactive-reset' ? restart : null }
  const window = eventTarget({ ...clock, parent: { postMessage: data => forwarded.push(JSON.parse(JSON.stringify(data))) } })
  if (standalone) window.parent = window
  const html = buildInteractiveContainer(original, { standalone, autoHeight: true })
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  vm.runInNewContext(script, { document, window })
  const send = (data, source = childWindow) => window.emit('message', { data, source })
  return { clock, forwarded, frame, document, window, restart, send }
}

test('outer resize bridge validates source and exact finite height, coalesces messages, and permits shrinking', () => {
  const harness = containerHarness()
  for (const height of [NaN, Infinity, -1, 0, 79, 24001, 120.5, '300']) harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height })
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 400, extra: true })
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 400 }, {})
  assert.equal(harness.clock.tasks.size, 0)
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 900 })
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 950 })
  assert.equal(harness.clock.tasks.size, 1)
  harness.clock.flush()
  assert.equal(harness.frame.style.height, '950px')
  assert.equal(harness.document.body.style.height, '950px')
  assert.equal(harness.forwarded.at(-1).height, 950)
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 200 }); harness.clock.flush()
  assert.equal(harness.frame.style.height, '200px')
  assert.equal(harness.forwarded.at(-1).height, 200)
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 200 }); harness.clock.flush()
  assert.equal(harness.forwarded.length, 2)
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 1200 }); harness.window.emit('pagehide')
  assert.equal(harness.clock.tasks.size, 0)
  assert.equal(harness.window.handlers.get('message').size, 0)
});

test('standalone adapts its header plus content locally without self-posting, and reset restores source', () => {
  const harness = containerHarness(true)
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 300 }); harness.clock.flush()
  assert.equal(harness.frame.style.height, '300px')
  assert.equal(harness.document.body.style.height, '364px')
  assert.equal(harness.forwarded.length, 0)
  harness.frame.srcdoc = 'changed'; harness.restart.emit('click'); assert.equal(harness.frame.srcdoc, 'original')
  harness.send({ type: INTERACTIVE_RESIZE_MESSAGE, height: 120 }); harness.clock.flush()
  assert.equal(harness.document.body.style.height, '184px')
  assert.equal(harness.forwarded.length, 0)
  assert.equal(isInteractiveResizeMessage({ type: INTERACTIVE_RESIZE_MESSAGE, height: INTERACTIVE_MIN_HEIGHT }), true)
  assert.equal(isInteractiveResizeMessage({ type: INTERACTIVE_RESIZE_MESSAGE, height: INTERACTIVE_MAX_HEIGHT }), true)
  assert.equal(isInteractiveResizeMessage({ type: INTERACTIVE_RESIZE_MESSAGE, height: Infinity }), false)
  assert.equal(isInteractiveResizeMessage({ type: INTERACTIVE_RESIZE_MESSAGE, height: 100, ready: true }), false)
});

test('empty and partial HTML fail clearly; large complete HTML, fenced documents and safe filenames work', () => {
  assert.match(validateInteractiveHtml('').error, /为空/)
  assert.match(validateInteractiveHtml('<button>go</button>').error, /完整 HTML/)
  const large = original.replace('</body>', `<!--${'大型完整页面'.repeat(400000)}--><script>window.largePageEnd = true;</script></body>`)
  assert.ok(large.length > 2000000)
  const validated = validateInteractiveHtml(large)
  assert.equal(validated.error, '')
  assert.equal(validated.html, large, 'do not truncate any original CSS, JS, or content by character count')
  const prepared = prepareInteractiveHtml(large, '大型互动网页')
  assert.equal(prepared.html, large)
  const wrappedInner = unescapeAttribute(prepared.preview.match(/ srcdoc="([^"]*)"/)[1])
  assert.ok(wrappedInner.endsWith(large), 'large page must retain its complete original source in the isolated frame')
  assert.equal(validateInteractiveHtml(`\`\`\`html\n${original}\n\`\`\``).html, original)
  assert.equal(interactiveFilename('../参数:实验.html'), '..参数实验.html')
  assert.equal(interactiveFilename(''), 'NetMind互动教学.html')
});
