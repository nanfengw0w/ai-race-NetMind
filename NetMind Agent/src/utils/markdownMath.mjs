import { Marked } from 'marked'
import katex from 'katex'

// Parse math before Markdown consumes backslash delimiters. Marked still owns
// code spans/fences and raw HTML, so their literal contents are never scanned.
function mathAtStart(source, block = false) {
  const delimiters = block ? [['$$', '$$'], ['\\[', '\\]']] : [['$$', '$$'], ['\\[', '\\]'], ['\\(', '\\)'], ['$', '$']]
  for (const [left, right] of delimiters) {
    if (!source.startsWith(left)) continue
    let end = left.length
    while ((end = source.indexOf(right, end)) >= 0) {
      let slashes = 0
      for (let i = end - 1; i >= 0 && source[i] === '\\'; i--) slashes++
      if (slashes % 2) { end += right.length; continue }
      const text = source.slice(left.length, end)
      if (!text.trim()) return null
      if (left === '$' && (/\n|^\s|\s$|[。；！？`]/.test(text) || /^\d[\d.,]*(?:\s+[A-Za-z]+)*$/.test(text) || /\d/.test(source[end + 1] || ''))) return null
      return { raw: source.slice(0, end + right.length), text, display: left === '$$' || left === '\\[' }
    }
  }
  return null
}

export function parseMarkdownMath(content) {
  const formulas = []
  const renderer = token => {
    if (token.literal) return token.raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    const index = formulas.push({ text: token.text, display: token.display }) - 1
    return `<span data-markdown-math="${index}"></span>${token.block ? '\n' : ''}`
  }
  const parser = new Marked({ breaks: true, gfm: true })
  parser.use({ extensions: [
    {
      name: 'displayMath', level: 'block',
      start: source => source.search(/(?:^|\n)(?:\$\$|\\\[)/),
      tokenizer(source) {
        const token = mathAtStart(source, true)
        if (token) return { ...token, type: 'displayMath', block: true }
      }, renderer,
    },
    {
      name: 'inlineMath', level: 'inline',
      start: source => source.search(/\$|\\[([]/),
      tokenizer(source) {
        const token = mathAtStart(source)
        if (token) return { ...token, type: 'inlineMath', literal: this.lexer.state.inRawBlock }
      }, renderer,
    },
  ] })
  return { html: parser.parse(String(content || '')), formulas }
}

export function renderMathFormula(text, display = false) {
  return katex.renderToString(text, {
    displayMode: display, throwOnError: false, trust: false,
    strict: 'ignore', maxExpand: 1000, maxSize: 20,
    output: 'htmlAndMathml', macros: {},
  })
}

// Run only after sanitizing the Markdown HTML. Generated KaTeX markup requires
// its layout styles; untrusted HTML never receives a style exemption.
export function renderMarkdownMath(element, formulas) {
  element.querySelectorAll('[data-markdown-math]').forEach(node => {
    const formula = formulas[Number(node.getAttribute('data-markdown-math'))]
    node.removeAttribute('data-markdown-math')
    if (!formula) return
    node.className = formula.display ? 'markdown-math-display' : 'markdown-math-inline'
    node.innerHTML = renderMathFormula(formula.text, formula.display)
  })
}
