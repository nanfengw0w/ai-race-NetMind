import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { JSDOM } from 'jsdom'
import { DECK_SANDBOX, enhanceHtmlDeck, getHtmlDeckTitle, installDeckPager, renderHtmlDeck } from './htmlDeck.js'

const unescape = value => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&')
const unwrap = value => unescape(value.match(/ srcdoc="([^"]*)"/)[1])

test('model-authored layouts, long text, all list items and script survive pagination without a content template', () => {
  const text = '完整讲解内容'.repeat(400)
  const source = `<!doctype html><html data-netmind-deck="v2"><head><title>自由课件</title><style>.diagram{display:grid;grid-template-columns:2fr 1fr}.timeline{display:flex}</style></head><body><section class="slide diagram"><h1>${text}</h1><ul>${Array.from({ length: 17 }, (_, index) => `<li>原始要点 ${index}</li>`).join('')}</ul><svg><path d="M0 0L10 20" /></svg></section><section class="slide timeline"><canvas id="animation"></canvas></section><script>window.example = '模型原始交互';</script></body></html>`
  const enhanced = enhanceHtmlDeck(source)
  assert.equal(enhanced.replace(/<script id="netmind-deck-pager">[\s\S]*?<\/script>\n/, ''), source)
  assert.equal((enhanced.match(/<li>/g) || []).length, 17)
  assert.ok(enhanced.includes(text))
  assert.doesNotMatch(enhanced, /机制|边界|takeaway|detail-card/)
  assert.equal(getHtmlDeckTitle(source), '自由课件')
})

test('new presentation mechanics are injected once and existing stored decks keep their own presentation code', () => {
  const source = '<html data-netmind-deck="v2"><head></head><body><section class="slide">一页也可完整演示</section></body></html>'
  const enhanced = enhanceHtmlDeck(source)
  assert.equal(enhanceHtmlDeck(enhanced), enhanced)
  const legacy = '<html><head><title>原有课件</title></head><body><main id="deck"><section class="slide">旧版内容</section></main><script>window.legacyNavigation = true;</script></body></html>'
  assert.equal(enhanceHtmlDeck(legacy), legacy)
  const inner = unwrap(renderHtmlDeck(legacy))
  assert.ok(inner.endsWith(legacy))
  assert.doesNotMatch(inner, /id="netmind-deck-pager"/)
})

test('complete floating-dot template keeps its controller without a duplicate generic pager', () => {
  const source = '<!doctype html><html data-netmind-deck="v2" data-netmind-navigation="legacy-dots"><head><title>自由布局</title><style>.asymmetric{display:grid;grid-template-columns:2fr 3fr}</style></head><body><main id="deck"><section class="slide asymmetric">完整内容</section><section class="slide">另一页</section></main><nav id="nav"></nav><div id="hint">← → 翻页 · B 静态 · ESC 索引</div><script id="netmind-ppt-navigation">window.templateNavigation = true;</script></body></html>'
  const enhanced = enhanceHtmlDeck(source)
  assert.ok(enhanced.endsWith(source))
  assert.equal(enhanceHtmlDeck(enhanced), enhanced)
  const inner = unwrap(renderHtmlDeck(source))
  assert.ok(inner.endsWith(source), 'complete model document and its controller must remain unchanged')
  assert.doesNotMatch(inner, /id="netmind-deck-pager"/)
  assert.doesNotMatch(inner, /id="netmind-deck-navigation"/)
  assert.match(inner, /"preserveDeckEscape":true/)
  assert.match(inner, /Content-Security-Policy/)
})

test('template viewport repair keeps authored overflow rules and inner clipping intact', () => {
  const source = '<!doctype html><html data-netmind-deck="v2" data-netmind-navigation="legacy-dots"><head><style>#deck > #s8{overflow:hidden}.diagram{overflow:hidden}</style></head><body><main id="deck"><section class="slide" id="s8"><div class="diagram">完整图形和后半内容</div></section></main><nav id="nav"></nav><script id="netmind-ppt-navigation">window.originalController=true</script></body></html>'
  const enhanced = enhanceHtmlDeck(source)
  assert.equal(enhanced.replace(/^<style id="netmind-template-viewport-scroll">[^<]*<\/style>\n/, ''), source)
  assert.equal(enhanceHtmlDeck(enhanced), enhanced)
  assert.equal((enhanced.match(/id="netmind-template-viewport-scroll"/g) || []).length, 1)
  assert.doesNotMatch(enhanced, /netmind-deck-pager|netmind-deck-navigation/)
  const { window } = new JSDOM(enhanced)
  assert.equal(window.getComputedStyle(window.document.querySelector('#s8')).overflow, 'auto')
  assert.equal(window.getComputedStyle(window.document.querySelector('.diagram')).overflow, 'hidden')
  window.close()
  for (const options of [{}, { preview: true }]) {
    const inner = unwrap(renderHtmlDeck(source, options))
    assert.ok(inner.replace('<script>window.__NETMIND_EMBED_PREVIEW__=true;</script>\n', '').endsWith(source))
    assert.match(inner, /#deck > section\.slide\{overflow:auto!important\}/)
    assert.match(renderHtmlDeck(source, options), /sandbox="allow-scripts"/)
  }
})

test('a navigation marker without its real controller and controls still gets the saved-deck fallback', () => {
  const source = '<html data-netmind-deck="v2" data-netmind-navigation="legacy-dots"><head></head><body><section class="slide">只有内容</section></body></html>'
  assert.match(enhanceHtmlDeck(source), /<script id="netmind-deck-pager">/)
})

test('preview and saved deck share the same isolated content, with trusted network blocking before model code', () => {
  const source = '<!doctype html><html data-netmind-deck="v2"><head><title>隔离演示</title></head><body><section class="slide"><button onclick="this.textContent=42">操作</button></section></body></html>'
  const rendered = renderHtmlDeck(source, { title: '隔离演示' })
  const inner = unwrap(rendered)
  assert.equal(DECK_SANDBOX, 'allow-scripts')
  assert.match(rendered, /sandbox="allow-scripts"/)
  assert.doesNotMatch(rendered, /sandbox="[^"]*(?:allow-same-origin|allow-top-navigation|allow-popups)/)
  assert.ok(inner.indexOf('Content-Security-Policy') < inner.indexOf('onclick='))
  assert.match(inner, /connect-src &#39;none&#39;|connect-src 'none'/)
  assert.match(rendered, /frame-src about:/)
  assert.ok(inner.includes(enhanceHtmlDeck(source)))
  assert.equal(unwrap(renderHtmlDeck(source, { preview: true })).replace('<script>window.__NETMIND_EMBED_PREVIEW__=true;</script>\n', ''), inner)
  const forgedMarker = source.replace('data-netmind-deck="v2"', 'data-netmind-deck="v2" data-netmind-ppt-container')
  assert.match(renderHtmlDeck(forgedMarker), /frame-src about:/, 'model metadata cannot bypass the trusted sandbox wrapper')
})

function pagerHarness(count) {
  const listeners = new Map()
  const byId = new Map()
  function element(tag) {
    const attributes = new Map()
    const styles = new Map()
    return {
      tag, attributes, styles, children: [], parentElement: null,
      style: { setProperty: (name, value, priority) => styles.set(name, { value, priority }) },
      setAttribute: (name, value) => attributes.set(name, value),
      addEventListener(type, listener) { this[type] = listener },
      append(...children) { this.children.push(...children) },
      appendChild(child) { this.children.push(child); if (child.id) byId.set(child.id, child) },
    }
  }
  const body = element('body')
  const pages = Array.from({ length: count }, (_, index) => Object.assign(element('section'), { parentElement: body, originalContent: `第 ${index + 1} 页完整内容`, layout: index % 2 ? 'grid' : 'flex' }))
  const document = {
    body, head: element('head'), documentElement: element('html'), readyState: 'complete',
    getElementById: id => byId.get(id),
    querySelectorAll: selector => selector === 'section.slide' ? pages : [],
    createElement: element,
    addEventListener: (type, listener) => listeners.set(type, listener),
  }
  const events = []
  const window = { addEventListener: (type, listener) => listeners.set(type, listener), removeEventListener: type => listeners.delete(type), getComputedStyle: () => { throw new Error('Pager must not freeze responsive computed styles') }, dispatchEvent: event => events.push(event) }
  const context = { document, window, CustomEvent: function (type, options) { return { type, ...options } } }
  vm.runInNewContext(`(${installDeckPager.toString()})();`, context)
  const navigation = byId.get('netmind-deck-navigation')
  return { pages, document, events, context, navigation, previous: navigation.children[0], progress: navigation.children[1], next: navigation.children[2], key: listeners.get('keydown') }
}

test('generic pager supports arbitrary slide counts and preserves each inner layout without adding cards', () => {
  for (const count of [1, 2, 17]) {
    const { pages, previous, progress, next, document, context } = pagerHarness(count)
    assert.equal(progress.textContent, `1 / ${count}`)
    assert.equal(previous.disabled, true)
    assert.equal(next.disabled, count === 1)
    pages.forEach((page, index) => {
      assert.equal(page.hidden, index !== 0)
      assert.equal(page.inert, index !== 0)
      assert.equal(page.originalContent, `第 ${index + 1} 页完整内容`)
      assert.equal(page.styles.has('--netmind-slide-display'), false)
      assert.equal(page.styles.has('display'), false)
      assert.equal(page.children.length, 0)
    })
    for (let index = 0; index < count + 2; index += 1) next.click()
    assert.equal(progress.textContent, `${count} / ${count}`)
    assert.equal(next.disabled, true)
    assert.equal(pages[count - 1].hidden, false)
    vm.runInNewContext(`(${installDeckPager.toString()})();`, context)
    assert.equal(document.body.children.length, 1)
    const css = document.head.children[0].textContent
    assert.match(css, /overflow:auto!important/)
    assert.doesNotMatch(css, /line-clamp|text-overflow|grid-template|\.card/)
    assert.match(css, /section\.slide\[data-netmind-page="inactive"\]\{display:none!important\}/)
    const activeFrameRule = css.match(/section\.slide\[data-netmind-page="active"\]\{([^}]+)\}/)[1]
    assert.doesNotMatch(activeFrameRule, /(?:^|;)display:/, 'active pages keep the model responsive display rules')
  }
})

test('responsive media queries remain live when a slide changes from grid to flex after initialization', () => {
  const source = '<html data-netmind-deck="v2"><head><style>.slide{display:grid;grid-template-columns:1fr 2fr}@media(max-width:700px){.slide{display:flex;flex-direction:column}}</style></head><body><section class="slide"><h1>响应式内容</h1></section></body></html>'
  assert.ok(enhanceHtmlDeck(source).includes('<style>.slide{display:grid;grid-template-columns:1fr 2fr}@media(max-width:700px){.slide{display:flex;flex-direction:column}}</style>'))
  const { pages, next, previous } = pagerHarness(2)
  pages[0].layout = 'grid'
  next.click()
  pages[0].layout = 'flex'
  previous.click()
  assert.equal(pages[0].hidden, false)
  assert.equal(pages[0].layout, 'flex')
  assert.equal(pages[0].styles.size, 0, 'pager never pins a computed display or grid/flex layout inline')
})

test('keyboard paging leaves editable controls and interactive diagrams in control of their own keys', () => {
  const { key, progress } = pagerHarness(4)
  let prevented = 0
  const event = { key: 'ArrowRight', target: { closest: () => null }, preventDefault: () => { prevented += 1 } }
  key(event)
  assert.equal(progress.textContent, '2 / 4')
  key({ ...event, target: { closest: () => ({ tag: 'input' }) } })
  key({ ...event, defaultPrevented: true })
  key({ ...event, ctrlKey: true })
  key({ ...event, key: 'ArrowDown' })
  assert.equal(progress.textContent, '2 / 4')
  assert.equal(prevented, 1)
  key({ ...event, key: 'End' })
  assert.equal(progress.textContent, '4 / 4')
  key({ ...event, key: 'Home' })
  assert.equal(progress.textContent, '1 / 4')
})
