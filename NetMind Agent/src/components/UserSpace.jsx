import { useCallback, useEffect, useRef, useState } from 'react'
import { getUserSpace } from '../services/api'
import styles from './UserSpace.module.scss'

function numberValue(value) {
  const number = Number(value)
  return value !== null && value !== undefined && Number.isFinite(number) && number >= 0 ? number : null
}

function formatCount(value) {
  const number = numberValue(value)
  return number === null ? '—' : Math.floor(number).toLocaleString('zh-CN')
}

function formatDuration(value) {
  const seconds = numberValue(value)
  if (seconds === null) return '—'
  if (seconds < 60) return `${Math.floor(seconds)}秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}分钟`
  const hours = Math.floor(minutes / 60)
  return minutes % 60 ? `${hours}小时${minutes % 60}分` : `${hours}小时`
}

function formatDate(value, withTime = false) {
  if (!value) return ''
  const text = String(value).trim()
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(text)
    ? `${text}T12:00:00`
    : /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text.replace(' ', 'T') : `${text.replace(' ', 'T')}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.valueOf())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric', day: 'numeric', ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  }).format(date)
}

function contributionDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return null
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(year, month - 1, day, 12)
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null
}

function contributionKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function buildContributionWeeks(days) {
  const records = new Map()
  for (const day of Array.isArray(days) ? days : []) {
    const seconds = numberValue(day?.seconds)
    if (!contributionDate(day?.date) || seconds === null) continue
    // The color encodes recorded duration, using the existing backend thresholds.
    const level = seconds <= 0 ? 0 : seconds < 600 ? 1 : seconds < 1800 ? 2 : seconds < 3600 ? 3 : 4
    records.set(day.date, { ...day, seconds, level })
  }
  const dates = [...records.keys()].sort()
  if (!dates.length) return []
  const first = contributionDate(dates[0])
  const last = contributionDate(dates[dates.length - 1])
  first.setDate(first.getDate() - first.getDay())
  last.setDate(last.getDate() + 6 - last.getDay())
  const weeks = []
  let week = []
  for (const date = new Date(first); date <= last; date.setDate(date.getDate() + 1)) {
    week.push(records.get(contributionKey(date)) || null)
    if (week.length === 7) { weeks.push(week); week = [] }
  }
  return weeks
}

function ContributionWall({ contributions }) {
  const records = contributions || {}
  const weeks = buildContributionWeeks(records.days)
  const monthFormatter = new Intl.DateTimeFormat('zh-CN', { month: 'short' })
  const monthLabels = weeks.map((week, index) => {
    const firstDay = week.find(Boolean)
    if (!firstDay) return ''
    const previousDay = index > 0 ? weeks[index - 1].find(Boolean) : null
    return !previousDay || firstDay.date.slice(0, 7) !== previousDay.date.slice(0, 7)
      ? monthFormatter.format(contributionDate(firstDay.date)) : ''
  })
  const hasActivity = weeks.some(week => week.some(day => day && day.seconds > 0))

  return <section className={styles.contribution} aria-labelledby="contribution-wall-title">
    <div className={styles.contributionHeader}>
      <div><span>LEARNING CONTRIBUTIONS</span><h3 id="contribution-wall-title">学习贡献墙</h3><p>按日期回看页面记录的学习时长。</p></div>
      <div className={styles.contributionSummary}>
        <strong>{formatCount(records.active_days)}</strong><span>活跃天数</span>
        <strong>{formatCount(records.current_streak)}</strong><span>连续学习</span>
      </div>
    </div>
    {weeks.length ? <>
      <div className={styles.calendarFrame} role="region" aria-label="学习活动日历，可横向滚动" tabIndex={0}>
        <div className={styles.calendarChart} style={{ '--week-count': weeks.length }}>
          <div className={styles.calendarMonths} aria-hidden="true">{monthLabels.map((label, index) => <span key={index}>{label}</span>)}</div>
          <div className={styles.calendarBody}>
            <div className={styles.weekdayLabels} aria-hidden="true"><span /><span>一</span><span /><span>三</span><span /><span>五</span><span /></div>
            <div className={styles.calendarGrid}>
              {weeks.map((week, weekIndex) => <div className={styles.calendarWeek} key={weekIndex}>
                {week.map((day, dayIndex) => <span key={day?.date || `${weekIndex}-${dayIndex}`} className={`${styles.calendarDay} ${day ? styles[`level${day.level}`] : styles.outside}`} role={day ? 'img' : undefined} aria-label={day ? `${day.date}，记录时长 ${formatDuration(day.seconds)}` : undefined} aria-hidden={day ? undefined : true} title={day ? `${day.date} · ${formatDuration(day.seconds)}` : undefined} />)}
              </div>)}
            </div>
          </div>
        </div>
      </div>
      <div className={styles.calendarLegend}><span>少</span>{[0, 1, 2, 3, 4].map(level => <i className={`${styles.calendarDay} ${styles[`level${level}`]}`} aria-hidden="true" key={level} />)}<span>多</span></div>
      {!hasActivity && <p className={styles.calendarEmpty}>这段时间还没有记录到学习时长。</p>}
    </> : <p className={styles.calendarEmpty}>暂无可显示的活动日期记录。</p>}
  </section>
}

function initialPanelWidth() {
  const maxWidth = Math.max(680, window.innerWidth - 24)
  try {
    const saved = Number(window.localStorage.getItem('netmind_user_space_width'))
    if (Number.isFinite(saved) && saved >= 680) return Math.min(saved, maxWidth)
  } catch { /* The panel still works when browser storage is unavailable. */ }
  return Math.min(1040, maxWidth)
}

export default function UserSpace({ userId, onClose, onOpenSession }) {
  const [response, setResponse] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [panelWidth, setPanelWidth] = useState(initialPanelWidth)
  const panelRef = useRef(null)
  const resizeCleanup = useRef(null)
  const requestKey = `${userId}:${attempt}`
  const current = response?.key === requestKey ? response : null
  const data = current?.data
  const error = current?.error

  useEffect(() => {
    let active = true
    getUserSpace(userId)
      .then(result => {
        if (active) setResponse({ key: requestKey, data: result })
      })
      .catch(() => {
        if (active) setResponse({ key: requestKey, error: '学习记录暂时加载失败，请重试。' })
      })
    return () => { active = false }
  }, [userId, requestKey])

  useEffect(() => {
    try { window.localStorage.setItem('netmind_user_space_width', String(panelWidth)) } catch { /* Optional preference. */ }
  }, [panelWidth])

  useEffect(() => () => resizeCleanup.current?.(), [])

  useEffect(() => {
    const onKeyDown = event => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const handleResizePointerDown = useCallback(event => {
    if (event.button !== 0) return
    event.preventDefault()
    const panel = panelRef.current
    if (!panel) return
    resizeCleanup.current?.()
    const startX = event.clientX
    const startWidth = panel.getBoundingClientRect().width
    const previousCursor = document.body.style.cursor
    const previousSelection = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = moveEvent => {
      const maxWidth = Math.max(680, window.innerWidth - 24)
      setPanelWidth(Math.max(680, Math.min(maxWidth, startWidth + startX - moveEvent.clientX)))
    }
    const onUp = () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelection
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      resizeCleanup.current = null
    }
    resizeCleanup.current = onUp
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }, [])

  const totals = data?.totals || {}
  const sessions = Array.isArray(data?.recent_sessions) ? data.recent_sessions.filter(session => session?.student_id) : []

  return (
    <div className={styles.overlay} role="presentation" onMouseDown={onClose}>
      <section
        ref={panelRef}
        className={styles.panel}
        style={{ width: panelWidth, minWidth: panelWidth }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="learning-record-title"
        onMouseDown={event => event.stopPropagation()}
      >
        <div className={styles.resizeHandle} role="separator" aria-label="调整学习记录面板宽度" title="拖拽调整宽度" onPointerDown={handleResizePointerDown} />
        <div className={styles.panelScroll}>
          <header className={styles.header}>
            <div>
              <span>LEARNING RECORDS</span>
              <h2 id="learning-record-title">学习记录</h2>
              <p>回看讨论过的问题，继续已有的学习内容。</p>
            </div>
            <button type="button" className={styles.closeButton} aria-label="关闭学习记录" title="关闭学习记录" onClick={onClose}>×</button>
          </header>

          {!current && <div className={styles.loading} role="status">正在读取学习记录…</div>}
          {error && <div className={styles.error} role="alert"><p>{error}</p><button type="button" className={styles.retryButton} onClick={() => setAttempt(value => value + 1)}>重新加载</button></div>}

          {data && (
            <div className={styles.body}>
              <div className={styles.metrics}>
                <div><span>学习对话</span><strong>{formatCount(totals.sessions)}<small>次</small></strong></div>
                <div><span>已生成资源</span><strong>{formatCount(totals.resources)}<small>个</small></strong></div>
                <div><span>记录时长</span><strong>{formatDuration(totals.learning_seconds)}</strong></div>
              </div>

              <ContributionWall contributions={data.contributions} />

              <section className={styles.sessions}>
                <div className={styles.sectionHeading}><h3>最近的学习对话</h3><span>打开对话，查看其中的讲解与资源</span></div>
                {sessions.length ? <div className={styles.sessionList}>
                  {sessions.map(session => (
                    <button type="button" key={session.student_id} onClick={() => onOpenSession(session.student_id)}>
                      <div className={styles.sessionContent}>
                        <strong>{session.title || '未命名对话'}</strong>
                        <time dateTime={session.updated_at}>{formatDate(session.updated_at, true)}</time>
                      </div>
                      <span className={styles.sessionResources}>{formatCount(session.resource_count)} 个资源</span>
                      <span className={styles.sessionArrow} aria-hidden="true">↗</span>
                    </button>
                  ))}
                </div> : <div className={styles.empty}><h4>还没有学习对话</h4><p>从一个问题开始，讨论和生成的资源会留在这里。</p></div>}
              </section>

              <p className={styles.note}>时长来自页面活动记录，仅用于回看学习过程。提问、浏览或生成资源不会在这里换算成掌握度分数。</p>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
