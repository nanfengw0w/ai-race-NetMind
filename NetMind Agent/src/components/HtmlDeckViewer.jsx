import { useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  DECK_PERMISSIONS,
  DECK_SANDBOX,
  downloadHtmlDeck,
  getHtmlDeckTitle,
  isDeckEscapeMessage,
  renderHtmlDeck,
  PPT_PAGE_GOTO,
  isPptPageMessage,
} from '../utils/htmlDeck'
import { addBookmarkNote } from '../utils/notes'
import { createRemoteNote, recordLearningEvent } from '../services/api'
import styles from './HtmlDeckViewer.module.scss'

function HtmlPreview({ content, preview = false, page = 1, syncAttempt = 0, onPageChange, onPageUnavailable, active = true, onLoad, frameRef, title = 'PPT课件预览' }) {
  const ownFrameRef = useRef(null)
  const targetRef = frameRef || ownFrameRef
  // A new artifact version remounts this frame. Page changes only send a goto
  // message: rebuilding srcDoc would reset animations and lose keyboard focus.
  const [document] = useState(() => {
    const channel = crypto.randomUUID()
    return { channel, html: renderHtmlDeck(content, { title, preview, pageSync: { channel, initialPage: page } }) }
  })
  useEffect(() => {
    const timeout = active ? window.setTimeout(() => onPageUnavailable?.(), 5000) : null
    const receive = event => {
      if (!active || event.source !== targetRef.current?.contentWindow
        || !isPptPageMessage(event.data, document.channel)) return
      window.clearTimeout(timeout)
      onPageChange?.({ page: event.data.page, total: event.data.total })
    }
    window.addEventListener('message', receive)
    return () => { window.clearTimeout(timeout); window.removeEventListener('message', receive) }
  }, [active, targetRef, document.channel, onPageChange, onPageUnavailable, syncAttempt])
  useEffect(() => {
    targetRef.current?.contentWindow?.postMessage({ type: PPT_PAGE_GOTO, channel: document.channel, page }, '*')
  }, [page, active, targetRef, document.channel, syncAttempt])
  return (
    <iframe
      ref={targetRef}
      title={title}
      className={styles.previewFrame}
      srcDoc={document.html}
      sandbox={DECK_SANDBOX}
      allow={DECK_PERMISSIONS}
      referrerPolicy="no-referrer"
      onLoad={() => {
        targetRef.current?.contentWindow?.postMessage({ type: PPT_PAGE_GOTO, channel: document.channel, page }, '*')
        onLoad?.()
      }}
    />
  )
}

export default function HtmlDeckViewer({ resource, studentId, userId, page = 1, syncAttempt = 0, onPageChange, onPageUnavailable }) {
  const title = useMemo(
    () => getHtmlDeckTitle(resource.content, resource.title),
    [resource.content, resource.title],
  )
  const documentKey = `${resource.id}:${resource.version || resource.current_version || 1}:${resource.content}`
  const previewStartedAt = useRef(0)
  const dialogRef = useRef(null)
  const modalFrameRef = useRef(null)
  const closeRef = useRef(null)
  const openRef = useRef(null)
  const dialogTitleId = useId()
  const [bookmarked, setBookmarked] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)

  useEffect(() => {
    if (!modalOpen) return
    const dialog = dialogRef.current
    const opener = openRef.current
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    if (!dialog.open) dialog.showModal()
    closeRef.current?.focus()
    const receive = event => {
      if (event.source === modalFrameRef.current?.contentWindow && isDeckEscapeMessage(event.data)) setModalOpen(false)
    }
    window.addEventListener('message', receive)
    return () => {
      window.removeEventListener('message', receive)
      if (dialog.open) dialog.close()
      document.body.style.overflow = originalOverflow
      if (opener?.isConnected) opener.focus()
    }
  }, [modalOpen])

  const handleBookmark = () => {
    const note = addBookmarkNote(title, resource.content)
    createRemoteNote(userId, { ...note, studentId }).catch(() => {})
    recordLearningEvent(userId, {
      student_id: studentId,
      event_type: 'note_added',
      resource_id: resource.id,
      resource_type: 'ppt',
      title,
      topic: title,
      metadata: {
        source: 'html_deck_bookmark',
        content: title,
      },
    }).catch(() => {})
    setBookmarked(true)
  }

  const handleOpen = () => {
    recordLearningEvent(userId, {
      student_id: studentId,
      event_type: 'resource_open',
      resource_id: resource.id,
      resource_type: 'ppt',
      title,
      topic: title,
      metadata: { source: 'html_deck_fullscreen' },
    }).catch(() => {})
    setModalOpen(true)
  }

  const handleDownload = () => {
    recordLearningEvent(userId, {
      student_id: studentId,
      event_type: 'resource_download',
      resource_id: resource.id,
      resource_type: 'ppt',
      title,
      topic: title,
      metadata: { source: 'html_deck_download' },
    }).catch(() => {})
    downloadHtmlDeck(resource.content, title)
  }

  useEffect(() => {
    previewStartedAt.current = performance.now()
    console.log('[NetMind Timing][PPT viewer] preview_source_ready', {
      source_chars: resource.content?.length || 0,
    })
  }, [resource.content])

  return (
    <div className={styles.htmlDeckViewer}>
      <div className={styles.toolbar}>
        <div className={styles.heading}>
          <span>PPT COURSEWARE</span>
          <strong title={title}>{title}</strong>
        </div>
        <div className={styles.actions}>
          <button ref={openRef} type="button" onClick={handleOpen}>全屏预览</button>
          <button type="button" className={styles.primary} onClick={handleDownload}>下载保存</button>
          <button
            type="button"
            onClick={handleBookmark}
            disabled={bookmarked}
            title={bookmarked ? '已收藏' : '收藏到笔记'}
          >
            {bookmarked ? '已收藏' : '收藏'}
          </button>
        </div>
      </div>

      <div className={styles.previewShell}>
        <HtmlPreview
          key={documentKey}
          content={resource.content} title="PPT课件预览" preview
          page={page} syncAttempt={syncAttempt} onPageChange={onPageChange} onPageUnavailable={onPageUnavailable} active={!modalOpen}
          onLoad={() => console.log('[NetMind Timing][PPT viewer] iframe_loaded', {
            elapsed_ms: Math.round(performance.now() - previewStartedAt.current),
          })}
        />
      </div>
      <dialog
        ref={dialogRef}
        className={styles.fullscreenDialog}
        aria-modal="true"
        aria-labelledby={dialogTitleId}
        onCancel={event => { event.preventDefault(); setModalOpen(false) }}
        onClose={() => setModalOpen(false)}
      >
        <div className={styles.dialogContents}>
          <header className={styles.dialogHeader}>
            <h2 id={dialogTitleId}>{title}</h2>
            <div className={styles.actions}>
              <button type="button" onClick={handleDownload}>下载保存</button>
              <button ref={closeRef} type="button" aria-label="关闭全屏预览" onClick={() => setModalOpen(false)}>关闭预览</button>
            </div>
          </header>
          {modalOpen && <HtmlPreview key={documentKey} frameRef={modalFrameRef} content={resource.content}
            page={page} syncAttempt={syncAttempt} onPageChange={onPageChange} onPageUnavailable={onPageUnavailable} title={`${title} · PPT全屏预览`} />}
        </div>
      </dialog>
    </div>
  )
}
