import createDOMPurify from 'dompurify'

const sanitizers = new WeakMap()

export function sanitizeHtml(html, targetWindow = window) {
  if (!sanitizers.has(targetWindow)) sanitizers.set(targetWindow, createDOMPurify(targetWindow))
  return sanitizers.get(targetWindow).sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'iframe', 'object', 'embed', 'link', 'meta'],
    FORBID_ATTR: ['style'],
  })
}
