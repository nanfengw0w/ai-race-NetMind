import { useEffect, useRef } from 'react'
import { sanitizeHtml } from '../utils/markdown'
import { parseMarkdownMath, renderMarkdownMath } from '../utils/markdownMath.mjs'
import 'katex/dist/katex.min.css'
import { cx } from '../utils/classNames'
import styles from './MarkdownContent.module.scss'
import { mountPdfCitations } from '../utils/pdfCitations.mjs'

export default function MarkdownContent({ content, className = '', variant = 'default', pdfCitations, onCitationClick }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !content) return
    try {
      const { html, formulas } = parseMarkdownMath(content)
      ref.current.innerHTML = sanitizeHtml(html)
      renderMarkdownMath(ref.current, formulas)
      mountPdfCitations(ref.current, pdfCitations, styles.pdfCitation)
      if (variant === 'document') {
        ref.current.querySelectorAll('table').forEach(table => {
          const scroller = document.createElement('div')
          scroller.className = styles.tableScroll
          table.before(scroller)
          scroller.appendChild(table)
        })
      }
    } catch {
      ref.current.textContent = content
    }
  }, [content, variant, pdfCitations])

  if (!content) return null
  const openCitation = event => {
    const id = event.target.closest('button[data-pdf-citation]')?.dataset.pdfCitation
    const source = pdfCitations?.find(item => item.id === id)
    if (source) onCitationClick?.(source)
  }
  return <div ref={ref} onClick={openCitation} className={cx(styles.markdownBody, variant === 'document' && styles.document, className)} />
}
