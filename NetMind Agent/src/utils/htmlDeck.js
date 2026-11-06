import { buildInteractiveContainer, INTERACTIVE_PERMISSIONS, INTERACTIVE_SANDBOX, isInteractiveEscapeMessage } from './interactiveHtml.mjs'
export { PPT_PAGE_STATE, PPT_PAGE_GOTO, isPptPageMessage } from './pptPageBridge.mjs'

export { INTERACTIVE_PERMISSIONS as DECK_PERMISSIONS, INTERACTIVE_SANDBOX as DECK_SANDBOX, isInteractiveEscapeMessage as isDeckEscapeMessage }

function text(value = '') {
  return String(value ?? '').trim()
}

function escapeHtml(value = '') {
  return text(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function safeId(value = 'deck') {
  return text(value)
    .replace(/[\\/:*?"<>|]/g, '')
    .replace(/\s+/g, '-')
    .slice(0, 48) || 'netmind-html-deck'
}

function stripFence(value = '') {
  const raw = String(value || '').trim()
  const fenced = raw.match(/^```(?:html)?\s*([\s\S]*?)```$/i)
  return fenced?.[1]?.trim() || raw
}

function extractHtml(value = '') {
  const raw = stripFence(value)
  if (!raw) return ''

  const doctypeIndex = raw.toLowerCase().indexOf('<!doctype')
  if (doctypeIndex >= 0) return raw.slice(doctypeIndex).trim()

  const htmlStart = raw.toLowerCase().indexOf('<html')
  const htmlEnd = raw.toLowerCase().lastIndexOf('</html>')
  if (htmlStart >= 0 && htmlEnd > htmlStart) {
    return `<!doctype html>\n${raw.slice(htmlStart, htmlEnd + 7).trim()}`
  }

  return ''
}

function patchSandboxSensitiveHtml(html = '') {
  return String(html || '')
    .replace(
      'const stored = localStorage.getItem(KEY);',
      'let stored = null; try{ stored = localStorage.getItem(KEY); }catch(_){ stored = null; }',
    )
    .replace(
      "if(opts.persist !== false) localStorage.setItem(KEY, window.__lowPowerMode ? '1' : '0');",
      "if(opts.persist !== false){ try{ localStorage.setItem(KEY, window.__lowPowerMode ? '1' : '0'); }catch(_){} }",
    )
    .replace(
      "addEventListener('wheel',e=>{\n  wheelAcc+=e.deltaY+e.deltaX;",
      "addEventListener('wheel',e=>{\n  e.preventDefault();\n  wheelAcc+=e.deltaY+e.deltaX;",
    )
    .replace(
      "clearTimeout(wheelTO);wheelTO=setTimeout(()=>wheelAcc=0,150);\n},{passive:true});",
      "clearTimeout(wheelTO);wheelTO=setTimeout(()=>wheelAcc=0,150);\n},{passive:false});",
    )
}

function patchEmbeddedPreviewHtml(html = '') {
  const marker = '<script>window.__NETMIND_EMBED_PREVIEW__=true;</script>'
  return String(html || '').replace(/<\/head\s*>/i, `${marker}\n</head>`)
}

/** Presentation mechanics only: model-authored slide contents and layouts stay intact. */
export function installDeckPager() {
  function initialize() {
    if (document.getElementById('netmind-deck-navigation')) return
    const pages = Array.from(document.querySelectorAll('section.slide'))
    if (!pages.length) return
    let index = 0
    const style = document.createElement('style')
    style.textContent = `
      html[data-netmind-paging="ready"],html[data-netmind-paging="ready"] body{box-sizing:border-box!important;width:100%!important;height:100%!important;margin:0!important;padding:0!important;overflow:hidden!important}
      html[data-netmind-paging="ready"] body{display:block!important}
      html[data-netmind-paging="ready"] section.slide[data-netmind-page="inactive"]{display:none!important}
      html[data-netmind-paging="ready"] section.slide[data-netmind-page="active"]{box-sizing:border-box!important;position:relative!important;inset:auto!important;width:100%!important;min-width:0!important;max-width:none!important;height:calc(100dvh - 38px)!important;min-height:0!important;max-height:none!important;margin:0!important;overflow:auto!important;flex:none!important;visibility:visible!important;opacity:1!important}
      #netmind-deck-navigation{box-sizing:border-box!important;position:fixed!important;inset:auto 0 0!important;z-index:2147483647!important;display:flex!important;align-items:center!important;justify-content:center!important;gap:18px!important;height:38px!important;margin:0!important;padding:4px 12px!important;border:0!important;border-top:1px solid #ddd!important;background:#fff!important;color:#282828!important;font:12px/1.4 "Microsoft YaHei",sans-serif!important;letter-spacing:normal!important}
      #netmind-deck-navigation button{box-sizing:border-box!important;position:static!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;min-width:66px!important;height:28px!important;margin:0!important;padding:3px 10px!important;border:1px solid #d6d6d6!important;border-radius:4px!important;background:#fff!important;color:#333!important;font:inherit!important;cursor:pointer!important;transform:none!important}
      #netmind-deck-navigation button:disabled{opacity:.4!important;cursor:default!important}
      #netmind-deck-navigation button:focus-visible{outline:2px solid #b43b31!important;outline-offset:2px!important}
      #netmind-deck-navigation output{min-width:62px!important;text-align:center!important;font:inherit!important;color:inherit!important}
    `
    document.head.appendChild(style)
    const navigation = document.createElement('nav')
    navigation.id = 'netmind-deck-navigation'
    navigation.setAttribute('aria-label', '课件翻页')
    const previous = document.createElement('button')
    previous.type = 'button'; previous.textContent = '上一页'
    const progress = document.createElement('output')
    progress.setAttribute('aria-live', 'polite')
    progress.setAttribute('aria-label', '课件页码')
    const next = document.createElement('button')
    next.type = 'button'; next.textContent = '下一页'
    pages.forEach((page, pageIndex) => {
      page.setAttribute('data-netmind-page', 'inactive')
      page.setAttribute('role', 'group')
      page.setAttribute('aria-roledescription', '幻灯片')
      page.setAttribute('aria-label', `第 ${pageIndex + 1} 页`)
      // Wrapped pages retain their DOM ancestry and selectors. Only the page
      // viewport is sized; no inner grid, paragraph or diagram is rewritten.
      let ancestor = page.parentElement
      while (ancestor && ancestor !== document.body) {
        for (const [name, value] of Object.entries({ height: 'calc(100dvh - 38px)', 'min-height': '0', 'max-height': 'none', width: '100%', overflow: 'visible', transform: 'none' })) ancestor.style.setProperty(name, value, 'important')
        ancestor = ancestor.parentElement
      }
    })
    function show(target) {
      index = Math.max(0, Math.min(pages.length - 1, target))
      pages.forEach((page, pageIndex) => {
        const active = pageIndex === index
        page.setAttribute('data-netmind-page', active ? 'active' : 'inactive')
        page.hidden = !active; page.inert = !active
      })
      previous.disabled = index === 0; next.disabled = index === pages.length - 1
      progress.textContent = `${index + 1} / ${pages.length}`
      window.dispatchEvent(new CustomEvent('netmind:slidechange', { detail: { index, total: pages.length } }))
    }
    previous.addEventListener('click', () => show(index - 1))
    next.addEventListener('click', () => show(index + 1))
    const goto = event => {
      if (Number.isSafeInteger(event.detail?.page) && event.detail.page > 0) show(event.detail.page - 1)
    }
    window.addEventListener('netmind:slidegoto', goto)
    window.addEventListener('pagehide', () => window.removeEventListener('netmind:slidegoto', goto), { once: true })
    navigation.append(previous, progress, next)
    document.body.appendChild(navigation)
    document.documentElement.setAttribute('data-netmind-paging', 'ready')
    document.addEventListener('keydown', event => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.target?.closest('input,textarea,select,button,a,[contenteditable]:not([contenteditable="false"]),[role="slider"]')) return
      const targets = { ArrowRight: index + 1, PageDown: index + 1, ArrowLeft: index - 1, PageUp: index - 1, Home: 0, End: pages.length - 1 }
      if (!(event.key in targets)) return
      event.preventDefault(); show(targets[event.key])
    })
    show(0)
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true })
  else initialize()
}

export function enhanceHtmlDeck(html) {
  if (!/<html\b[^>]*\bdata-netmind-deck\s*=\s*["']v2["']/i.test(html) || /id=["']netmind-deck-pager["']/i.test(html)) return html
  // The model now receives the complete floating-dot controller. Keep its
  // page transitions, overview and static-mode controls instead of adding a
  // second pager over them. Previously saved v2 documents still use the
  // generic fallback below when that controller is absent.
  const templateNavigation = /<html\b[^>]*\bdata-netmind-navigation\s*=\s*["']legacy-dots["']/i.test(html)
    && /<script\b[^>]*\bid\s*=\s*["']netmind-ppt-navigation["']/i.test(html)
    && /<(?:main|div)\b[^>]*\bid\s*=\s*["']deck["']/i.test(html)
    && /<(?:nav|div)\b[^>]*\bid\s*=\s*["']nav["']/i.test(html)
  if (templateNavigation) {
    // Runtime-only viewport repair: preserve every byte of the authored
    // document, inner clipping and its own controller. Model-specific slide
    // selectors must not make overflowed teaching content unreachable.
    if (/id=["']netmind-template-viewport-scroll["']/i.test(html)) return html
    const viewportStyle = '<style id="netmind-template-viewport-scroll">html[data-netmind-navigation="legacy-dots"] #deck > section.slide{overflow:auto!important}</style>\n'
    return viewportStyle + html
  }
  const script = `<script id="netmind-deck-pager">(${installDeckPager.toString()})();</script>`
  return html.replace(/<\/body\s*>/i, `${script}\n</body>`)
}

function renderUnavailableDeck(title = 'PPT课件') {
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>
  *{box-sizing:border-box}
  html,body{width:100%;height:100%;margin:0}
  body{display:grid;place-items:center;background:#f1efea;color:#0a0a0b;font-family:"PingFang SC","Microsoft YaHei",sans-serif}
  main{width:min(560px,calc(100vw - 48px));border-top:3px solid #0a0a0b;padding-top:22px}
  p{color:#66615a;line-height:1.8}
</style>
</head>
<body>
  <main>
    <h1>课件格式已失效</h1>
    <p>旧版 JSON 课件已经停用，请重新生成 PPT课件。</p>
  </main>
</body>
</html>`
}

export function renderHtmlDeck(content, options = {}) {
  const html = typeof content === 'string' ? extractHtml(content) : ''
  let rendered = html ? enhanceHtmlDeck(patchSandboxSensitiveHtml(html)) : renderUnavailableDeck(options.title)
  if (options.preview) rendered = patchEmbeddedPreviewHtml(rendered)
  return buildInteractiveContainer(rendered, { title: options.title || getHtmlDeckTitle(html), autoHeight: false, preserveDeckEscape: true, pageSync: options.pageSync })
    .replace('<html lang="zh-CN">', '<html lang="zh-CN" data-netmind-ppt-container>')
}

export function getHtmlDeckTitle(content, fallbackTitle = 'PPT课件') {
  const html = typeof content === 'string' ? extractHtml(content) : ''
  const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)
  return titleMatch?.[1]?.replace(/<[^>]*>/g, '').trim() || fallbackTitle
}

export function downloadHtmlDeck(content, title) {
  const startedAt = performance.now()
  const html = renderHtmlDeck(content, { title })
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${safeId(title || getHtmlDeckTitle(content))}.html`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
  console.log('[NetMind Timing][PPT viewer] download_html', {
    elapsed_ms: Math.round(performance.now() - startedAt),
    html_chars: html.length,
  })
}

export function openHtmlDeck(content, title) {
  // Notes use this entry point outside the React viewer. Keep that preview in
  // the same application document and isolation boundary as the main viewer.
  const html = renderHtmlDeck(content, { title })
  const opener = document.activeElement
  const dialog = document.createElement('dialog')
  dialog.setAttribute('aria-label', `${title || 'PPT课件'} · 全屏预览`)
  Object.assign(dialog.style, { width: 'calc(100vw - 32px)', height: 'calc(100dvh - 32px)', maxWidth: 'none', maxHeight: 'none', padding: '0', border: '1px solid #ddd', borderRadius: '8px', overflow: 'hidden' })
  const contents = document.createElement('div')
  Object.assign(contents.style, { display: 'flex', flexDirection: 'column', height: '100%' })
  const close = document.createElement('button')
  close.textContent = '关闭预览'; close.type = 'button'
  Object.assign(close.style, { alignSelf: 'flex-end', flex: 'none', margin: '8px 12px', padding: '6px 12px' })
  const frame = document.createElement('iframe')
  frame.title = `${title || 'PPT课件'} · 全屏预览`; frame.srcdoc = html
  frame.setAttribute('sandbox', INTERACTIVE_SANDBOX); frame.setAttribute('allow', INTERACTIVE_PERMISSIONS); frame.referrerPolicy = 'no-referrer'
  Object.assign(frame.style, { width: '100%', flex: '1', minHeight: '0', border: '0' })
  contents.append(close, frame); dialog.append(contents); document.body.append(dialog)
  const overflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  const receive = event => { if (event.source === frame.contentWindow && isInteractiveEscapeMessage(event.data)) dialog.close() }
  const cleanup = () => { window.removeEventListener('message', receive); document.body.style.overflow = overflow; dialog.remove(); if (opener?.isConnected) opener.focus() }
  close.addEventListener('click', () => dialog.close())
  dialog.addEventListener('close', cleanup, { once: true })
  window.addEventListener('message', receive)
  dialog.showModal(); close.focus()
  return dialog
}
