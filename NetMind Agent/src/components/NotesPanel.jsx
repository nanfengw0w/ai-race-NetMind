import { useState, useEffect, useCallback, useRef } from 'react'
import { getNotes, deleteNote } from '../utils/notes'
import { deleteRemoteNote, getRemoteNotes, syncRemoteNotes } from '../services/api'
import { openHtmlDeck } from '../utils/htmlDeck'
import styles from './NotesPanel.module.scss'

const TYPE_LABELS = {
  text: '文字摘录',
  quiz: '练习题',
  bookmark: '课件收藏',
}

const TYPE_ICONS = {
  text: 'T',
  quiz: 'Q',
  bookmark: 'B',
}

function formatTime(iso) {
  const d = new Date(iso)
  const month = d.getMonth() + 1
  const day = d.getDate()
  const hour = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${month}月${day}日 ${hour}:${min}`
}

function NoteItem({ note, onDelete, onPreviewBookmark }) {
  return (
    <div className={styles.noteItem}>
      <div className={styles.noteHeader}>
        <span className={styles.noteType}>
          <i>{TYPE_ICONS[note.type] || 'N'}</i>
          {TYPE_LABELS[note.type] || '笔记'}
        </span>
        <span className={styles.noteTime}>{formatTime(note.createdAt)}</span>
      </div>

      {note.type === 'text' && (
        <div className={styles.noteContent}>
          <p>{note.content}</p>
          {note.source && <small>来源：{note.source}</small>}
        </div>
      )}

      {note.type === 'quiz' && (
        <div className={styles.noteContent}>
          <p className={styles.quizQuestion}>{note.content}</p>
          {note.options && note.options.length > 0 && (
            <div className={styles.quizOptions}>
              {note.options.map((opt, i) => (
                <div key={i} className={styles.quizOption}>
                  <span className={styles.optionLetter}>{String.fromCharCode(65 + i)}</span>
                  {opt}
                </div>
              ))}
            </div>
          )}
          {note.answer && (
            <div className={styles.quizAnswer}>
              <strong>正确答案：</strong>
              <span>{note.answer}</span>
            </div>
          )}
          {note.explanation && (
            <div className={styles.quizExplain}>{note.explanation}</div>
          )}
        </div>
      )}

      {note.type === 'bookmark' && (
        <div className={styles.noteContent}>
          <p className={styles.bookmarkTitle}>{note.content}</p>
          {note.html && (
            <button
              className={styles.previewBtn}
              onClick={() => onPreviewBookmark(note)}
            >
              预览课件
            </button>
          )}
        </div>
      )}

      <div className={styles.noteActions}>
        <button className={styles.deleteBtn} onClick={() => onDelete(note.id)}>
          删除
        </button>
      </div>
    </div>
  )
}

function mergeNotes(localNotes, remoteNotes) {
  const map = new Map()
  for (const note of [...remoteNotes, ...localNotes]) {
    if (!note?.id) continue
    map.set(note.id, note)
  }
  return [...map.values()].sort((a, b) => new Date(b.createdAt || 0) - new Date(a.createdAt || 0))
}

function NotesView({ userId = 'local_user', studentId, onClose }) {
  const [notes, setNotes] = useState(getNotes)
  const [filter, setFilter] = useState('all')
  const [panelWidth, setPanelWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem('netmind_notes_width'))
    return Number.isFinite(saved) && saved >= 360 ? saved : 520
  })
  const panelRef = useRef(null)

  useEffect(() => {
    let active = true
    const localNotes = getNotes()
    syncRemoteNotes(userId, studentId, localNotes)
      .catch(() => null)
      .then(() => getRemoteNotes(userId))
      .then(remoteNotes => {
        if (active) setNotes(mergeNotes(localNotes, remoteNotes || []))
      })
      .catch(() => {})
    return () => { active = false }
  }, [studentId, userId])

  useEffect(() => {
    window.localStorage.setItem('netmind_notes_width', String(panelWidth))
  }, [panelWidth])

  const handleResizePointerDown = useCallback((e) => {
    e.preventDefault()
    const panel = panelRef.current
    if (!panel) return
    const startX = e.clientX
    const startWidth = panel.getBoundingClientRect().width

    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (moveE) => {
      const next = Math.max(360, Math.min(900, startWidth + (moveE.clientX - startX)))
      setPanelWidth(next)
    }
    const onUp = () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }, [])

  const handleDelete = (id) => {
    deleteNote(id)
    setNotes(current => current.filter(note => note.id !== id))
    deleteRemoteNote(userId, id).catch(() => {})
  }

  const handlePreviewBookmark = (note) => {
    openHtmlDeck(note.html, note.content)
  }

  const filtered = filter === 'all'
    ? notes
    : notes.filter(n => n.type === filter)

  return (
    <div className={styles.overlay} role="presentation" onMouseDown={onClose}>
      <section
        ref={panelRef}
        className={styles.panel}
        style={{ width: panelWidth, minWidth: panelWidth }}
        role="dialog"
        aria-modal="true"
        aria-label="我的笔记"
        onMouseDown={e => e.stopPropagation()}
      >
        <div className={styles.panelScroll}>
          <header className={styles.header}>
            <div>
              <span>MY NOTES</span>
              <h2>我的笔记</h2>
              <p>选中文字右键、练习题、课件均可加入笔记</p>
            </div>
            <button type="button" title="关闭笔记" onClick={onClose}>
              &times;
            </button>
          </header>

          <div className={styles.filters}>
            {[
              { key: 'all', label: '全部' },
              { key: 'text', label: '文字摘录' },
              { key: 'quiz', label: '练习题' },
              { key: 'bookmark', label: '课件收藏' },
            ].map(f => (
              <button
                key={f.key}
                className={filter === f.key ? styles.activeFilter : ''}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
                {f.key !== 'all' && (
                  <em>{notes.filter(n => f.key === 'all' || n.type === f.key).length}</em>
                )}
              </button>
            ))}
          </div>

          <div className={styles.body}>
            {filtered.length === 0 && (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}>NM</div>
                <p>还没有笔记</p>
                <small>
                  选中页面上的文字后右键可添加摘录，
                  <br />
                  练习题和 PPT课件也有「加入笔记」按钮。
                </small>
              </div>
            )}
            {filtered.map(note => (
              <NoteItem
                key={note.id}
                note={note}
                onDelete={handleDelete}
                onPreviewBookmark={handlePreviewBookmark}
              />
            ))}
          </div>
        </div>
        <div
          className={styles.resizeHandle}
          role="separator"
          aria-label="调整笔记面板宽度"
          title="拖拽调整宽度"
          onPointerDown={handleResizePointerDown}
        />
      </section>
    </div>
  )
}

export default function NotesPanel(props) {
  return <NotesView key={`${props.userId || 'local_user'}:${props.studentId || ''}`} {...props} />
}
