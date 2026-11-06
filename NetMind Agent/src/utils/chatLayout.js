export const CHAT_WIDTH_KEYS = {
  desktop: 'netmind_chat_width',
  compact: 'netmind_chat_width_compact',
}

export const SPLIT_HANDLE_WIDTH = 9

export function clampChatWidth(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

export function parseChatWidth(value) {
  if (value === null || value === undefined || String(value).trim() === '') return null
  const width = Number(value)
  return Number.isFinite(width) && width > 0 ? width : null
}

// Rendering may clamp a preference to the available room; it never changes the preference.
export function getChatLayout(viewportWidth, availableWidth, preferences = {}) {
  const compact = viewportWidth <= 1800
  const mobile = viewportWidth <= 760
  const preferenceKey = compact ? 'compact' : 'desktop'
  const minWidth = compact ? 280 : 320
  const widthLimit = compact ? 520 : 680
  const maxWidth = Math.max(
    minWidth,
    Math.min(widthLimit, availableWidth - SPLIT_HANDLE_WIDTH - 560),
  )
  const defaultWidth = compact ? (viewportWidth <= 1100 ? 280 : 300) : 420
  const preferredWidth = parseChatWidth(preferences[preferenceKey]) ?? defaultWidth

  return {
    compact,
    mobile,
    preferenceKey,
    minWidth,
    maxWidth,
    width: clampChatWidth(preferredWidth, minWidth, maxWidth),
  }
}
