import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const template = readFileSync(new URL('../../../backend/assets/ppt_navigation_template.html', import.meta.url), 'utf8')
const script = template.match(/<script id="netmind-ppt-navigation">([\s\S]*?)<\/script>/)[1]

function controller(count = 3, setup = () => {}) {
  const listeners = new Map(), timers = new Map(), elements = new Map(), events = []
  let timerId = 0
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null
      this.attributes = new Map(); this.listeners = new Map(); this.dataset = {}; this.style = {}
      this.scrollLeft = 0; this.scrollTop = 0; this.clientWidth = 300; this.clientHeight = 600
      this.scrollWidth = 300; this.scrollHeight = 600; this.offsetWidth = 10; this.offsetLeft = 0
      const classes = new Set()
      this.classList = {
        contains: name => classes.has(name), add: name => classes.add(name),
        toggle(name, on = !classes.has(name)) { if (on) classes.add(name); else classes.delete(name) },
      }
    }
    set className(value) { value.split(/\s+/).forEach(name => this.classList.add(name)) }
    setAttribute(name, value) { this.attributes.set(name, value) }
    removeAttribute(name) { this.attributes.delete(name) }
    addEventListener(type, listener) { this.listeners.set(type, listener) }
    appendChild(child) { child.parentElement = this; child.offsetLeft = this.children.length * 18; this.children.push(child); return child }
    append(...children) { children.forEach(child => this.appendChild(child)) }
    replaceChildren(...children) { this.children = []; this.append(...children) }
    matches(selector) { return selector === 'section.slide' && this.tagName === 'SECTION' && this.classList.contains('slide') }
    contains(element) { return element === this || this.children.some(child => child.contains(element)) }
    closest() { return ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(this.tagName) ? this : null }
    querySelector() { return null }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')])
      if (selector === '*') return descendants
      if (selector === 'button') return descendants.filter(el => el.tagName === 'BUTTON')
      if (selector === '[data-anim]') return descendants.filter(el => el.dataset.anim !== undefined)
      const anim = selector.match(/^\[data-anim="([^"]+)"\]$/)?.[1]
      if (anim) return descendants.filter(el => el.dataset.anim === anim)
      return []
    }
    cloneNode() { const clone = new Element(this.tagName); clone.className = 'slide'; return clone }
    focus() { document.activeElement = this }
    scrollTo({ left }) { this.scrollLeft = Math.max(0, left) }
    scrollBy({ top }) { this.scrollTop = Math.max(0, Math.min(this.scrollHeight - this.clientHeight, this.scrollTop + top)) }
  }
  const document = {
    readyState: 'complete', activeElement: null, getAnimations: () => [],
    createElement: tag => new Element(tag), getElementById: id => elements.get(id),
  }
  document.body = new Element('body')
  for (const id of ['deck', 'nav', 'hint', 'netmind-ppt-status']) {
    const element = new Element(); element.id = id; elements.set(id, element); document.body.appendChild(element)
  }
  const deck = elements.get('deck'), nav = elements.get('nav')
  const slides = Array.from({ length: count }, () => { const slide = new Element('section'); slide.className = 'slide'; deck.appendChild(slide); return slide })
  const context = {
    document, Element, innerWidth: 1280, innerHeight: 720,
    getComputedStyle: () => ({ overflowX: 'auto', overflowY: 'auto' }),
    matchMedia: () => ({ matches: false }),
    localStorage: { getItem() { throw new Error('opaque origin') }, setItem() { throw new Error('opaque origin') } },
    CustomEvent: class { constructor(type, options) { this.type = type; Object.assign(this, options) } },
    addEventListener: (type, listener) => listeners.set(type, listener), dispatchEvent: event => events.push(event),
    requestAnimationFrame: callback => callback(),
    setTimeout: callback => { timers.set(++timerId, callback); return timerId }, clearTimeout: id => timers.delete(id),
  }
  context.window = context
  setup({ slides, Element })
  vm.runInNewContext(script, context)
  const flush = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(callback => callback()) }
  function event(type, values) {
    const value = { target: document.body, defaultPrevented: false, preventDefault() { this.defaultPrevented = true }, ...values }
    listeners.get(type)(value); return value
  }
  const key = (keyName, target = document.body) => event('keydown', { key: keyName, target })
  return { context, document, deck, nav, slides, events, flush, event, key, Element, status: elements.get('netmind-ppt-status') }
}

test('full template is standalone and supplies mechanics without content layouts or a page quota', () => {
  assert.match(template, /data-netmind-deck="v2" data-netmind-navigation="legacy-dots"/)
  assert.match(template, /<!-- SLIDES_HERE -->/)
  assert.doesNotMatch(template, /<section\b|<script\b[^>]*src=|<link\b|https?:\/\//)
  assert.doesNotMatch(template, /detail-card|feature-frame|grid-2-7-5|WebGL|bg-dark|bg-light/)
  assert.doesNotMatch(script, /scrollIntoView/)
})

test('legacy horizontal controller supports one or many slides with dots, keyboard bounds, and no ancestor scrolling', () => {
  for (const count of [1, 3, 40]) {
    const { context, deck, nav, slides, key, flush, status } = controller(count)
    assert.equal(nav.children.length, count)
    assert.equal(deck.style.width, `${count * 100}vw`)
    assert.equal(context.__currentSlideIndex, 0)
    key('End'); flush()
    assert.equal(context.__currentSlideIndex, count - 1)
    assert.equal(deck.style.transform, `translateX(${-(count - 1) * 100}vw)`)
    assert.equal(status.textContent, `第 ${count} 页，共 ${count} 页`)
    assert.equal(slides[count - 1].inert, false)
    key('ArrowRight'); flush()
    assert.equal(context.__currentSlideIndex, count - 1)
    if (count > 20) assert.ok(nav.scrollLeft > 0, 'long dot row scrolls only within its own navigation element')
    key('Home'); flush()
    assert.equal(context.__currentSlideIndex, 0)
    assert.equal(nav.scrollLeft, 0)
  }
})

test('page overflow scrolls before wheel/vertical-key paging and text controls retain their own keys', () => {
  const { context, document, slides, event, key, Element, flush } = controller()
  slides[0].scrollHeight = 1800
  const wheel = event('wheel', { target: slides[0], deltaY: 100, deltaX: 0, deltaMode: 0 })
  assert.equal(wheel.defaultPrevented, false)
  assert.equal(context.__currentSlideIndex, 0)
  key('PageDown')
  assert.ok(slides[0].scrollTop > 0, 'body focus still scrolls the active content page')
  assert.equal(context.__currentSlideIndex, 0)
  const input = new Element('input'); slides[0].appendChild(input)
  key('ArrowRight', input)
  assert.equal(context.__currentSlideIndex, 0)
  slides[0].scrollTop = 1200
  event('wheel', { target: slides[0], deltaY: 100, deltaX: 0, deltaMode: 0 }); flush()
  assert.equal(context.__currentSlideIndex, 1)
  key('ArrowRight', document.body); flush()
  assert.equal(context.__currentSlideIndex, 2)
})

test('Escape opens and closes an accessible slide index without changing the current slide', () => {
  const { context, document, key, deck, nav } = controller()
  const escape = key('Escape')
  const overview = document.body.children.find(el => el.id === 'overview')
  assert.equal(escape.defaultPrevented, true)
  assert.equal(overview.hidden, false)
  assert.equal(overview.attributes.get('aria-modal'), 'true')
  assert.equal(overview.querySelectorAll('button').length, 4)
  assert.equal(deck.inert, true)
  assert.equal(nav.inert, true)
  key('Escape')
  assert.equal(overview.hidden, true)
  assert.equal(deck.inert, false)
  assert.equal(nav.inert, false)
  assert.equal(context.__currentSlideIndex, 0)
})

test('B toggles preserve external animation state and custom slide animation hooks', () => {
  const { context, document, slides, events, key } = controller()
  const animation = { effect: { target: slides[0] }, playState: 'running', pause() { this.playState = 'paused' }, play() { this.playState = 'running' } }
  document.getAnimations = () => [animation]
  const played = []
  context.__playSlide = index => played.push(index)
  key('b')
  assert.equal(context.__lowPowerMode, true)
  assert.equal(animation.playState, 'paused')
  key('b')
  assert.equal(context.__lowPowerMode, false)
  assert.equal(animation.playState, 'running')
  assert.deepEqual(played, [0, 0])
  assert.equal(events.filter(event => event.type === 'ppt-low-power-change').length, 2)
})

test('pipeline keeps titles and body readable while advancing steps and arrows before paging', () => {
  const nodes = {}, animations = []
  const { context, key, flush } = controller(2, ({ slides, Element }) => {
    slides[0].dataset.animate = 'pipeline'
    for (const [name, anim] of [['title', 'fade'], ['body', 'fade'], ['first', 'step'], ['arrow', 'arrow'], ['second', 'step']]) {
      const element = new Element(name === 'title' ? 'h1' : 'p')
      element.dataset.anim = anim
      element.style.opacity = '0'
      element.animate = frames => {
        animations.push({ name, frames })
        return { cancel() {} }
      }
      nodes[name] = element
      slides[0].appendChild(element)
    }
  })
  assert.equal(context.__lowPowerMode, false)
  assert.equal(nodes.title.style.opacity, '1')
  assert.equal(nodes.body.style.opacity, '1')
  for (const name of ['first', 'arrow', 'second']) assert.equal(nodes[name].style.opacity, '0.72')
  key('ArrowRight')
  assert.equal(context.__currentSlideIndex, 0)
  assert.equal(nodes.first.style.opacity, '1')
  assert.equal(nodes.second.style.opacity, '0.72')
  assert.equal(nodes.arrow.style.opacity, '0.72')
  key('ArrowRight')
  assert.equal(context.__currentSlideIndex, 0)
  assert.equal(nodes.second.style.opacity, '1')
  assert.equal(nodes.arrow.style.opacity, '1')
  assert.deepEqual(animations.map(({ name }) => name), ['first', 'second', 'arrow'])
  for (const { frames } of animations) {
    assert.equal(frames[0].opacity, .72)
    assert.equal(frames[1].opacity, 1)
  }
  key('ArrowRight'); flush()
  assert.equal(context.__currentSlideIndex, 1)
})
