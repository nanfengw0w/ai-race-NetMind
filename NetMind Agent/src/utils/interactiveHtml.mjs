import { installPptInnerBridge, installPptContainerBridge, pptBridgeConfig } from './pptPageBridge.mjs'

/** Model code only runs in an opaque-origin sandbox, never in the application DOM. */
export const INTERACTIVE_SANDBOX = 'allow-scripts'
export const INTERACTIVE_PERMISSIONS = "camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; display-capture 'none'; payment 'none'; fullscreen 'none'"
export const INTERACTIVE_ESCAPE_MESSAGE = 'netmind-interactive:escape:v1'
export const INTERACTIVE_RESIZE_MESSAGE = 'netmind-interactive:resize:v1'
export const INTERACTIVE_MIN_HEIGHT = 80
export const INTERACTIVE_MAX_HEIGHT = 24000

export function isInteractiveEscapeMessage(data) {
  return Boolean(data && typeof data === 'object' && !Array.isArray(data) && data.type === INTERACTIVE_ESCAPE_MESSAGE && Object.keys(data).length === 1)
}

export function isInteractiveResizeMessage(data) {
  return Boolean(data && typeof data === 'object' && !Array.isArray(data) && Object.keys(data).length === 2 && data.type === INTERACTIVE_RESIZE_MESSAGE && typeof data.height === 'number' && Number.isFinite(data.height) && Number.isInteger(data.height) && data.height >= INTERACTIVE_MIN_HEIGHT && data.height <= INTERACTIVE_MAX_HEIGHT)
}

const SHARED_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'media-src data: blob:',
  'font-src data: blob:',
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "worker-src 'none'",
  "manifest-src 'none'",
]

export const INTERACTIVE_INNER_CSP = [...SHARED_CSP, "frame-src 'none'"].join('; ')
// The trusted container permits only local about:srcdoc documents. Its policy
// also prevents the inner frame from navigating itself to a network resource.
export const INTERACTIVE_CONTAINER_CSP = [...SHARED_CSP, 'frame-src about:'].join('; ')

export function escapeInteractiveAttribute(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

const TITLE_STYLE = '(?:后现代主义设计审美|瑞士国际主义|构成主义简洁设计审美|简洁美观)'
const TITLE_SEPARATOR = '[·•/|｜:：—–-]+'
const TITLE_STYLE_PREFIX = new RegExp(`^${TITLE_STYLE}\\s*${TITLE_SEPARATOR}\\s*(?=\\S)`)
const TITLE_STYLE_SUFFIX = new RegExp(`\\s*${TITLE_SEPARATOR}\\s*${TITLE_STYLE}$`)
const TITLE_STYLE_BRACKETS = [['(', ')'], ['（', '）'], ['[', ']'], ['【', '】']]
const TITLE_STYLE_ONLY = new RegExp(`^${TITLE_STYLE}$`)

/** Remove design annotations at title boundaries, preserving the learning topic. */
export function getInteractiveTitle(value, fallback = '互动教学') {
  let title = String(value || '').trim()
  let previous
  do {
    previous = title
    title = title.replace(TITLE_STYLE_PREFIX, '').replace(TITLE_STYLE_SUFFIX, '').trim()
    for (const [open, close] of TITLE_STYLE_BRACKETS) {
      if (title.endsWith(close)) {
        const start = title.lastIndexOf(open)
        if (start > 0 && TITLE_STYLE_ONLY.test(title.slice(start + open.length, -close.length).trim())) title = title.slice(0, start).trim()
      }
      if (title.startsWith(open)) {
        const end = title.indexOf(close)
        if (end > 0 && TITLE_STYLE_ONLY.test(title.slice(open.length, end).trim()) && title.slice(end + close.length).trim()) title = title.slice(end + close.length).trim()
      }
    }
  } while (title !== previous)
  return title || fallback
}

export function validateInteractiveHtml(content) {
  if (typeof content !== 'string' || !content.trim()) return { html: '', error: '互动教学内容为空，暂时无法预览。' }
  const html = content.trim().replace(/^```(?:html)?\s*\n?/i, '').replace(/\n?```\s*$/, '').trim()
  if (!/<html(?:\s[^>]*)?>/i.test(html) || !/<\/html\s*>/i.test(html) || !/<body(?:\s[^>]*)?>/i.test(html) || !/<\/body\s*>/i.test(html)) {
    return { html: '', error: '返回的内容不是完整 HTML 网页，请重新生成。' }
  }
  return { html, error: '' }
}

export function interactiveFilename(title) {
  const safe = String(title || 'NetMind互动教学').replace(/[<>:"/\\|?*]/g, '').split('').filter(char => char.charCodeAt(0) >= 32).join('').replace(/\.html?$/i, '').trim().slice(0, 100)
  return `${safe || 'NetMind互动教学'}.html`
}

/** Serialized into the opaque inner frame; no model code runs in its parent. */
export function installInteractiveInnerBridge(config) {
  let disposed = false, timer = null, animation = null, resizeObserver = null, mutationObserver = null
  let lastHeight = -1, lastWidth = -1, viewportUnit = ''
  const root = document.documentElement
  const viewportPattern = /(-?\d*\.?\d+)(?:dvh|svh|lvh|vh)\b/gi
  const sizeProperty = /^(?:height|min-height|max-height|block-size|min-block-size|max-block-size|top|bottom|inset(?:-.+)?|padding(?:-.+)?|margin(?:-.+)?|flex-basis|gap|row-gap)$/
  const setStyle = (style, name, value) => {
    if (style.getPropertyValue(name) !== value || style.getPropertyPriority(name) !== 'important') style.setProperty(name, value, 'important')
  }
  const escape = event => {
    if (event.key !== 'Escape' || event.defaultPrevented) return
    // PPT owns Escape for its slide overview. Only delegate when its actual
    // controller and controls exist; ordinary interactive pages and earlier
    // generic-pager decks retain Escape-to-close in the surrounding preview.
    if (config.preserveDeckEscape && document.getElementById('deck') && document.getElementById('nav')) {
      const template = root.getAttribute?.('data-netmind-navigation') === 'legacy-dots' && document.getElementById('netmind-ppt-navigation')
      const legacy = document.getElementById('overview') && document.getElementById('hint')
      if (template || legacy) return
    }
    window.parent.postMessage({ type: config.escapeType }, '*')
  }
  window.addEventListener('keydown', escape, true)

  function patchDeclaration(style) {
    if (!style) return
    Array.from(style).forEach(name => {
      if (!sizeProperty.test(name)) return
      const value = style.getPropertyValue(name)
      viewportPattern.lastIndex = 0
      if (!viewportPattern.test(value)) return
      viewportPattern.lastIndex = 0
      // A minimum of one viewport is not an intrinsic content constraint.
      const next = /^(?:min-height|min-block-size)$/.test(name) ? '0px' : value.replace(viewportPattern, 'calc(var(--netmind-embedded-vh) * $1)')
      style.setProperty(name, next, style.getPropertyPriority(name))
    })
  }

  function patchRules(rules) {
    Array.from(rules || []).forEach(rule => {
      if (rule.style) patchDeclaration(rule.style)
      if (rule.cssRules) patchRules(rule.cssRules)
    })
  }

  function normalizeViewport() {
    // Width is independent of the reported content height. Pin vh-based visual
    // dimensions to this stable embedding viewport, avoiding parent/child growth
    // feedback while still responding to a narrow or wide layout.
    const nextUnit = `${Math.min(900, Math.max(320, window.innerWidth * .72)) / 100}px`
    if (nextUnit !== viewportUnit) { viewportUnit = nextUnit; setStyle(root.style, '--netmind-embedded-vh', viewportUnit) }
    for (const sheet of Array.from(document.styleSheets || [])) {
      try { patchRules(sheet.cssRules) } catch { /* external styles are blocked by CSP */ }
    }
    for (const element of Array.from(document.querySelectorAll('[style]'))) patchDeclaration(element.style)
    for (const element of [root, document.body]) {
      if (!element) continue
      setStyle(element.style, 'height', 'auto'); setStyle(element.style, 'min-height', '0px'); setStyle(element.style, 'max-height', 'none')
      setStyle(element.style, 'block-size', 'auto'); setStyle(element.style, 'min-block-size', '0px'); setStyle(element.style, 'max-block-size', 'none')
    }
    if (document.body) setStyle(document.body.style, 'overflow', 'visible')
  }

  function measure() {
    if (disposed || !document.body) return
    normalizeViewport()
    const scrollY = window.scrollY || 0
    let bottom = 0
    function visit(element, isBody = false, clipTop = 0, clipBottom = Infinity) {
      const style = window.getComputedStyle(element)
      if (style.display === 'none' || element.hidden === true || (!isBody && style.position === 'fixed')) return
      const rectangle = element.getBoundingClientRect()
      if (isBody || rectangle.height > 0 || rectangle.width > 0) {
        const visibleBottom = Math.min(clipBottom, rectangle.bottom + scrollY + Math.max(0, parseFloat(style.marginBottom) || 0))
        if (visibleBottom > clipTop) bottom = Math.max(bottom, visibleBottom)
      }
      if (style.contentVisibility === 'hidden') return
      let children = Array.from(element.children || [])
      // Closed <details> can retain nonzero DOMRects for their UA-hidden
      // descendants. Only the first summary participates in the closed layout.
      if (element.tagName === 'DETAILS' && !element.open) children = children.filter(child => child.tagName === 'SUMMARY').slice(0, 1)
      if (!isBody && /^(?:hidden|clip|auto|scroll)$/.test(style.overflowY)) {
        clipTop = Math.max(clipTop, rectangle.top + scrollY)
        clipBottom = Math.min(clipBottom, rectangle.bottom + scrollY)
      }
      if (clipBottom <= clipTop) return
      children.forEach(child => visit(child, false, clipTop, clipBottom))
    }
    visit(document.body, true)
    // Never use documentElement.scrollHeight/clientHeight: their viewport floor
    // would lock a previously tall iframe at its old height after content shrinks.
    const natural = Math.ceil(Math.max(0, bottom) + 2)
    const height = Math.min(config.maxContentHeight, Math.max(config.minHeight, natural))
    setStyle(root.style, 'overflow-y', natural > config.maxContentHeight ? 'auto' : 'hidden')
    if (lastHeight < 0 || Math.abs(height - lastHeight) >= 2) {
      lastHeight = height
      window.parent.postMessage({ type: config.resizeType, height }, '*')
    }
  }

  function schedule() {
    if (disposed || timer !== null || animation !== null) return
    timer = window.setTimeout(() => {
      timer = null
      animation = window.requestAnimationFrame(() => { animation = null; measure() })
    }, 80)
  }

  function resize() {
    const width = Math.round(window.innerWidth)
    if (width === lastWidth) return
    lastWidth = width; schedule()
  }

  function initialize() {
    if (disposed || !config.autoHeight || !document.body) return
    normalizeViewport()
    if (typeof ResizeObserver !== 'undefined') { resizeObserver = new ResizeObserver(schedule); resizeObserver.observe(root); resizeObserver.observe(document.body) }
    if (typeof MutationObserver !== 'undefined') { mutationObserver = new MutationObserver(schedule); mutationObserver.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['style', 'class', 'hidden', 'open', 'width', 'height'] }) }
    document.addEventListener('load', schedule, true)
    window.addEventListener('resize', resize)
    if (document.fonts?.ready) document.fonts.ready.then(() => { if (!disposed) schedule() }).catch(() => {})
    lastWidth = Math.round(window.innerWidth); schedule()
  }

  function dispose() {
    if (disposed) return
    disposed = true
    if (timer !== null) window.clearTimeout(timer)
    if (animation !== null) window.cancelAnimationFrame(animation)
    resizeObserver?.disconnect(); mutationObserver?.disconnect()
    window.removeEventListener('keydown', escape, true); window.removeEventListener('resize', resize)
    document.removeEventListener('load', schedule, true); document.removeEventListener('DOMContentLoaded', initialize)
    window.removeEventListener('pagehide', dispose); window.removeEventListener('beforeunload', dispose)
  }
  if (config.autoHeight) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true })
    else initialize()
  }
  window.addEventListener('pagehide', dispose, { once: true }); window.addEventListener('beforeunload', dispose, { once: true })
}

/** Trusted middle frame validates the actual child WindowProxy before forwarding. */
export function installInteractiveContainerBridge(config) {
  const frame = document.getElementById('netmind-interactive-content')
  const restart = document.getElementById('netmind-interactive-reset')
  const header = document.getElementById('netmind-interactive-header')
  const original = frame.getAttribute('srcdoc')
  let disposed = false, timer = null, pendingHeight = config.minHeight, lastHeight = -1, observer = null
  function forward(data) { if (window.parent !== window) window.parent.postMessage(data, '*') }
  function apply() {
    timer = null
    if (disposed || !config.autoHeight) return
    const headerHeight = header ? Math.ceil(header.getBoundingClientRect().height) : 0
    frame.style.height = `${pendingHeight}px`
    const height = Math.min(config.maxHeight, pendingHeight + headerHeight)
    document.body.style.height = `${height}px`; document.documentElement.style.height = `${height}px`
    if (height !== lastHeight) { lastHeight = height; forward({ type: config.resizeType, height }) }
  }
  function schedule() { if (!disposed && timer === null) timer = window.setTimeout(apply, 60) }
  function message(event) {
    if (event.source !== frame.contentWindow || !event.data || typeof event.data !== 'object' || Array.isArray(event.data)) return
    const data = event.data
    if (data.type === config.escapeType && Object.keys(data).length === 1) { forward({ type: config.escapeType }); return }
    if (!config.autoHeight || data.type !== config.resizeType || Object.keys(data).length !== 2 || typeof data.height !== 'number' || !Number.isFinite(data.height) || !Number.isInteger(data.height) || data.height < config.minHeight || data.height > config.maxHeight) return
    pendingHeight = data.height; schedule()
  }
  function reset() { frame.srcdoc = original; lastHeight = -1 }
  function dispose() {
    if (disposed) return
    disposed = true
    if (timer !== null) window.clearTimeout(timer)
    observer?.disconnect()
    restart?.removeEventListener('click', reset)
    window.removeEventListener('message', message); window.removeEventListener('resize', schedule)
    window.removeEventListener('pagehide', dispose); window.removeEventListener('beforeunload', dispose)
  }
  restart?.addEventListener('click', reset)
  window.addEventListener('message', message)
  if (config.autoHeight) {
    window.addEventListener('resize', schedule)
    if (header && typeof ResizeObserver !== 'undefined') { observer = new ResizeObserver(schedule); observer.observe(header) }
  }
  window.addEventListener('pagehide', dispose, { once: true }); window.addEventListener('beforeunload', dispose, { once: true })
}

function innerDocument(html, autoHeight, preserveDeckEscape, pageConfig) {
  // Trusted parser input comes first. Do not search for a model-supplied <head>:
  // it could occur inside a comment or JS string and bypass policy injection.
  // The HTML parser naturally merges the following full document into this head.
  const config = { autoHeight, preserveDeckEscape, escapeType: INTERACTIVE_ESCAPE_MESSAGE, resizeType: INTERACTIVE_RESIZE_MESSAGE, minHeight: INTERACTIVE_MIN_HEIGHT, maxContentHeight: INTERACTIVE_MAX_HEIGHT - 512 }
  const bootstrap = `(${installInteractiveInnerBridge.toString()})(${JSON.stringify(config)});${pageConfig ? `(${installPptInnerBridge.toString()})(${JSON.stringify(pageConfig).replace(/</g, '\\u003c')});` : ''}`
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${escapeInteractiveAttribute(INTERACTIVE_INNER_CSP)}"><meta name="referrer" content="no-referrer"><script>${bootstrap}</script>${html}`
}

export function buildInteractiveContainer(content, { title = '互动教学', standalone = false, autoHeight = false, preserveDeckEscape = false, pageSync } = {}) {
  const { html, error } = validateInteractiveHtml(content)
  if (error) throw new Error(error)
  const safeTitle = escapeInteractiveAttribute(String(title || '互动教学').slice(0, 160))
  const pageConfig = pptBridgeConfig(pageSync)
  const frameDocument = escapeInteractiveAttribute(innerDocument(html, autoHeight, preserveDeckEscape, pageConfig))
  const containerConfig = { autoHeight, escapeType: INTERACTIVE_ESCAPE_MESSAGE, resizeType: INTERACTIVE_RESIZE_MESSAGE, minHeight: INTERACTIVE_MIN_HEIGHT, maxHeight: INTERACTIVE_MAX_HEIGHT }
  const containerScript = `(${installInteractiveContainerBridge.toString()})(${JSON.stringify(containerConfig)});${pageConfig ? `(${installPptContainerBridge.toString()})(${JSON.stringify(pageConfig).replace(/</g, '\\u003c')});` : ''}`
  const heightStyles = autoHeight ? `html,body{height:auto;min-height:0}html{overflow-x:hidden;overflow-y:${standalone ? 'auto' : 'hidden'}}body{display:block;overflow:visible}iframe{flex:none;height:${INTERACTIVE_MIN_HEIGHT}px;min-height:0}` : ''
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${escapeInteractiveAttribute(INTERACTIVE_CONTAINER_CSP)}">
<meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${safeTitle} · NetMind</title>
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#fbfbfa;color:#1a1a1a;font-family:"Helvetica Neue","PingFang SC","Microsoft YaHei",sans-serif}body{display:flex;flex-direction:column}header{flex-shrink:0;display:flex;align-items:center;gap:16px;padding:12px 20px;border-bottom:1px solid #e0e0e0;background:#fff}header div{min-width:0;flex:1}header strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:600 17px "Noto Serif SC",SimSun,serif}header small{display:block;margin-top:4px;font-size:10px;color:#777}button{padding:7px 12px;border:1px solid #ded5cb;border-radius:5px;background:#fff;color:#84251f;font-size:12px;cursor:pointer}button:focus-visible{outline:2px solid #b23a2f;outline-offset:3px}iframe{display:block;width:100%;flex:1;min-height:0;border:0;background:#fff}${heightStyles}</style></head>
<body>${standalone ? `<header id="netmind-interactive-header"><div><strong>${safeTitle}</strong><small>NetMind 互动教学 · 调整参数，观察结果；支持离线使用</small></div><button type="button" id="netmind-interactive-reset">重新开始</button></header>` : ''}
<iframe id="netmind-interactive-content" title="${safeTitle}" sandbox="${INTERACTIVE_SANDBOX}" referrerpolicy="no-referrer" allow="${escapeInteractiveAttribute(INTERACTIVE_PERMISSIONS)}" srcdoc="${frameDocument}"></iframe>
<script>${containerScript}</script></body></html>`
}

export function prepareInteractiveHtml(content, title) {
  const validation = validateInteractiveHtml(content)
  if (validation.error) return { ...validation, preview: '', standalone: '' }
  return {
    ...validation,
    preview: buildInteractiveContainer(validation.html, { title, autoHeight: true }),
    standalone: buildInteractiveContainer(validation.html, { title, standalone: true, autoHeight: true }),
  }
}
