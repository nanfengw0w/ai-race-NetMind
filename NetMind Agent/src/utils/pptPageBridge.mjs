export const PPT_PAGE_STATE = 'netmind-ppt:page:v1'
export const PPT_PAGE_GOTO = 'netmind-ppt:goto:v1'

export function isPptPageMessage(data, channel, type = PPT_PAGE_STATE) {
  const state = type === PPT_PAGE_STATE
  return Boolean(data && typeof data === 'object' && !Array.isArray(data)
    && Object.keys(data).length === (state ? 4 : 3) && data.type === type
    && typeof channel === 'string' && channel.length > 0 && data.channel === channel
    && Number.isSafeInteger(data.page) && data.page > 0
    && (!state || (Number.isSafeInteger(data.total) && data.total >= data.page)))
}

/** Runs in the opaque model frame. Read actual navigation state, never event detail. */
export function installPptInnerBridge(config) {
  let disposed = false, observer = null, timer = null, pending = null, deadline = 0, last = '', running = false, requested = false
  function read() {
    const dots = Array.from(document.querySelectorAll('#nav button[data-i]'))
    // The native index creates section.slide thumbnail clones outside #deck.
    // Count only the real deck pages, including while that index is open.
    const pages = Array.from(document.querySelectorAll(dots.length ? '#deck > section.slide' : 'section.slide'))
    let index = -1
    if (dots.length) {
      const current = dots.find(dot => dot.getAttribute('aria-current') === 'page')
      const value = current ? Number(current.getAttribute('data-i')) : window.__currentSlideIndex
      if (Number.isSafeInteger(value)) index = value
    } else index = pages.findIndex(page => page.getAttribute('data-netmind-page') === 'active')
    return index >= 0 && index < pages.length ? { page: index + 1, total: pages.length, dots } : null
  }
  function schedule() { if (!disposed && timer === null) timer = window.setTimeout(() => { timer = null; tick() }, 100) }
  function tick() {
    if (disposed || running) return
    running = true
    try {
    let state = read()
    if (pending !== null && state) {
      const target = Math.min(pending, state.total)
      if (state.page !== target && Date.now() < deadline) {
        if (state.dots.length) state.dots.find(dot => Number(dot.getAttribute('data-i')) === target - 1)?.click()
        else window.dispatchEvent(new CustomEvent('netmind:slidegoto', { detail: { page: target } }))
        state = read()
      }
      if (state?.page === target || Date.now() >= deadline) pending = null
    }
    if (pending !== null) { if (Date.now() >= deadline) pending = null; else { schedule(); return } }
    if (!state) return
    const key = `${state.page}/${state.total}`
    if (key !== last) { last = key; window.parent.postMessage({ type: config.stateType, channel: config.channel, page: state.page, total: state.total }, '*') }
    } finally { running = false }
  }
  function request(page) { last = ''; pending = page; deadline = Date.now() + 4000; tick() }
  const inputTypes = ['pointerdown', 'click', 'keydown', 'wheel', 'touchstart']
  function input(event) {
    // Programmatic navigation clicks have isTrusted=false. A real user takes
    // ownership immediately rather than fighting a pending retry after a lock.
    if (!event.isTrusted || pending === null) return
    pending = null; requested = true; schedule()
  }
  function message(event) {
    const data = event.data
    if (event.source !== window.parent || !data || typeof data !== 'object' || Array.isArray(data)
      || Object.keys(data).length !== 3 || data.type !== config.gotoType || data.channel !== config.channel
      || !Number.isSafeInteger(data.page) || data.page < 1) return
    requested = true; request(data.page)
  }
  function initialize() {
    if (disposed) return
    if (typeof MutationObserver !== 'undefined') {
      observer = new MutationObserver(tick)
      observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-current', 'data-netmind-page'] })
    }
    if (!requested) request(config.initialPage)
    else tick()
  }
  function dispose() {
    disposed = true; observer?.disconnect(); if (timer !== null) window.clearTimeout(timer)
    window.removeEventListener('message', message); window.removeEventListener('ppt-slide-change', tick)
    window.removeEventListener('netmind:slidechange', tick); document.removeEventListener('DOMContentLoaded', initialize)
    inputTypes.forEach(type => window.removeEventListener(type, input, true))
    window.removeEventListener('pagehide', dispose); window.removeEventListener('beforeunload', dispose)
  }
  window.addEventListener('message', message); window.addEventListener('ppt-slide-change', tick)
  window.addEventListener('netmind:slidechange', tick)
  inputTypes.forEach(type => window.addEventListener(type, input, true))
  window.addEventListener('pagehide', dispose); window.addEventListener('beforeunload', dispose)
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true })
  else initialize()
}

/** Separate trusted middle-frame listener; every forwarded payload is reconstructed. */
export function installPptContainerBridge(config) {
  const frame = document.getElementById('netmind-interactive-content')
  let last = ''
  function message(event) {
    const data = event.data
    if (!data || typeof data !== 'object' || Array.isArray(data) || data.channel !== config.channel
      || !Number.isSafeInteger(data.page) || data.page < 1) return
    if (event.source === frame.contentWindow && data.type === config.stateType && Object.keys(data).length === 4
      && Number.isSafeInteger(data.total) && data.total >= data.page) {
      const key = `${data.page}/${data.total}`
      if (key !== last) { last = key; window.parent.postMessage({ type: config.stateType, channel: config.channel, page: data.page, total: data.total }, '*') }
    } else if (event.source === window.parent && data.type === config.gotoType && Object.keys(data).length === 3) {
      last = ''
      frame.contentWindow.postMessage({ type: config.gotoType, channel: config.channel, page: data.page }, '*')
    }
  }
  function dispose() { window.removeEventListener('message', message); window.removeEventListener('pagehide', dispose); window.removeEventListener('beforeunload', dispose) }
  window.addEventListener('message', message); window.addEventListener('pagehide', dispose); window.addEventListener('beforeunload', dispose)
}

export function pptBridgeConfig(pageSync) {
  if (!pageSync || typeof pageSync.channel !== 'string' || !pageSync.channel.trim()) return null
  return { channel: pageSync.channel, initialPage: Number.isSafeInteger(pageSync.initialPage) && pageSync.initialPage > 0 ? pageSync.initialPage : 1, stateType: PPT_PAGE_STATE, gotoType: PPT_PAGE_GOTO }
}
