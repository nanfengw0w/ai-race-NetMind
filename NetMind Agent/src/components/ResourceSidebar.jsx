import { useState } from 'react'
import { FILTER_OPTIONS } from '../constants/resources'
import { cx } from '../utils/classNames'
import styles from './ResourceSidebar.module.scss'

export default function ResourceSidebar({ filter, setFilter, resources }) {
  const [expanded, setExpanded] = useState(true)
  const counts = {}
  resources.forEach(r => {
    counts[r.type] = (counts[r.type] || 0) + 1
  })

  return (
    <aside className={cx(styles.resourceSidebar, expanded && styles.expanded)} aria-label="资源分类">
      <div className={styles.sidebarHead}>
        <div className={styles.sidebarTitle}>资源分类</div>
        <button type="button" className={styles.sidebarToggle} onClick={() => setExpanded(value => !value)} aria-expanded={expanded} aria-label={expanded ? '收起资源分类' : '展开资源分类'} title={expanded ? '收起资源分类' : '展开资源分类'}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
        </button>
      </div>
      {FILTER_OPTIONS.filter(opt => !opt.legacy || counts[opt.key] > 0).map(opt => {
        const count = opt.key === 'all' ? resources.length : counts[opt.key] || 0
        const isEmpty = opt.key !== 'all' && count === 0

        return (
          <button
            key={opt.key}
            className={cx(styles.sidebarItem, filter === opt.key && styles.active, isEmpty && styles.empty)}
            onClick={() => setFilter(opt.key)}
            aria-pressed={filter === opt.key}
            title={`${opt.label} · ${count} 个资源`}
          >
            <span className={styles.sidebarMark} aria-hidden="true">{opt.mark}</span>
            <span className={styles.sidebarLabel}>{opt.label}</span>
            <span className={styles.sidebarCount}>{count}</span>
          </button>
        )
      })}
    </aside>
  )
}
