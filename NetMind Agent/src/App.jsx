import { useState, useEffect, useCallback, useRef } from 'react'
import ChatPanel from './components/ChatPanel'
import ResourcePanel from './components/ResourcePanel'
import PdfWorkspace from './components/PdfWorkspace'
import UserSpace from './components/UserSpace'
import NotesPanel from './components/NotesPanel'
import ContextMenu from './components/ContextMenu'
import SettingsPanel from './components/SettingsPanel'
import OnboardingTour from './components/OnboardingTour'
import {
  getSessions,
  deleteSession,
  getResources,
  getProfile as fetchProfile,
  recordLearningActivity,
  recordLearningEvent,
  createRemoteNote,
} from './services/api'
import { addTextNote } from './utils/notes'
import { cx } from './utils/classNames'
import { CHAT_WIDTH_KEYS, clampChatWidth, getChatLayout, parseChatWidth } from './utils/chatLayout'
import { artifactVersion, upsertArtifacts, updatedArtifactReference } from './utils/artifacts.mjs'
import styles from './App.module.scss'
import './styles/global.scss'

function createId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function mergeSessionResources(remoteResources = [], cachedResources = []) {
  if (!cachedResources.length) return remoteResources

  const remoteById = new Map(
    remoteResources
      .filter(resource => resource?.id !== undefined && resource?.id !== null)
      .map(resource => [String(resource.id), resource]),
  )
  const usedIds = new Set()
  const merged = cachedResources.map(resource => {
    const key = resource?.id !== undefined && resource?.id !== null
      ? String(resource.id)
      : ''
    const persisted = key ? remoteById.get(key) : null
    if (persisted) {
      usedIds.add(key)
      return artifactVersion(persisted) >= artifactVersion(resource) ? persisted : resource
    }
    return resource
  })

  remoteResources.forEach(resource => {
    const key = resource?.id !== undefined && resource?.id !== null
      ? String(resource.id)
      : ''
    if (!key || !usedIds.has(key)) merged.push(resource)
  })
  return merged
}

function getOrCreateUserId() {
  window.localStorage.setItem('netmind_user_id', 'local_user')
  return 'local_user'
}

function App() {
  const [resources, setResources] = useState([])
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [activeResource, setActiveResource] = useState(0)
  const [resourceReference, setResourceReference] = useState(null)
  const [pdfReference, setPdfReference] = useState(null)
  const [pdfLocation, setPdfLocation] = useState(null)
  const [rightView, setRightView] = useState('resources')
  const pdfWorkspaceRef = useRef(null)
  const tourRef = useRef(null)
  const tutorialComposerRef = useRef(null)
  const [mode, setMode] = useState('chat')
  const [userId] = useState(getOrCreateUserId)
  const [studentId, setStudentId] = useState(() => (
    window.localStorage.getItem('netmind_last_session_id') || createId('session')
  ))
  const [sessions, setSessions] = useState([])
  const [showSidebar, setShowSidebar] = useState(false)
  const [showUserSpace, setShowUserSpace] = useState(false)
  const [showNotes, setShowNotes] = useState(false)
  const [notesKey, setNotesKey] = useState(0)
  const [showSettings, setShowSettings] = useState(false)
  const [contextMenu, setContextMenu] = useState(null)
  const initialSessionId = useRef(studentId)
  const activeStudentIdRef = useRef(studentId)
  const sessionViewCacheRef = useRef(new Map())
  const resourcesRef = useRef(resources)
  const profileRef = useRef(profile)
  const loadingRef = useRef(loading)
  const activeResourceRef = useRef(activeResource)
  const mainRef = useRef(null)
  const splitHandleRef = useRef(null)
  const resizeCleanupRef = useRef(null)
  const [chatWidths, setChatWidths] = useState(() => ({
    desktop: parseChatWidth(window.localStorage.getItem(CHAT_WIDTH_KEYS.desktop)),
    compact: parseChatWidth(window.localStorage.getItem(CHAT_WIDTH_KEYS.compact)),
  }))
  const [layoutSize, setLayoutSize] = useState(() => ({
    viewportWidth: window.innerWidth,
    availableWidth: window.innerWidth,
  }))
  const chatLayout = getChatLayout(layoutSize.viewportWidth, layoutSize.availableWidth, chatWidths)
  const prepareTourStep = useCallback(step => {
    setShowSidebar(false)
    setShowNotes(false)
    setShowSettings(false)
    setShowUserSpace(false)
    setContextMenu(null)
    if (step.mode) {
      setMode(step.mode)
      if (step.mode === 'resource') setPdfReference(null)
    }
    if (step.view) setRightView(step.view)
  }, [])

  const loadSessions = useCallback(async () => {
    try {
      const data = await getSessions(userId)
      setSessions(data)
      return data
    } catch {
      setSessions([])
      return []
    }
  }, [userId])

  useEffect(() => {
    resourcesRef.current = resources
  }, [resources])

  useEffect(() => {
    profileRef.current = profile
  }, [profile])

  useEffect(() => {
    loadingRef.current = loading
  }, [loading])

  useEffect(() => {
    activeResourceRef.current = activeResource
  }, [activeResource])

  const persistSessionSnapshot = useCallback((sid) => {
    if (!sid) return
    const previous = sessionViewCacheRef.current.get(sid) || {}
    sessionViewCacheRef.current.set(sid, {
      ...previous,
      resources: previous.resources ?? resourcesRef.current,
      profile: previous.profile ?? profileRef.current,
      loading: previous.loading ?? loadingRef.current,
      activeResource: previous.activeResource ?? activeResourceRef.current,
    })
  }, [])

  const updateSessionField = useCallback((sid, field, updater) => {
    const previous = sessionViewCacheRef.current.get(sid) || {}
    const fallbacks = {
      resources: [],
      profile: null,
      loading: false,
      activeResource: 0,
    }
    const current = Object.prototype.hasOwnProperty.call(previous, field)
      ? previous[field]
      : fallbacks[field]
    const next = typeof updater === 'function' ? updater(current) : updater
    const updated = { ...previous, [field]: next }
    sessionViewCacheRef.current.set(sid, updated)

    if (activeStudentIdRef.current === sid) {
      if (field === 'resources') setResources(next)
      if (field === 'profile') setProfile(next)
      if (field === 'loading') setLoading(next)
      if (field === 'activeResource') setActiveResource(next)
    }
    return next
  }, [])

  const handleResourceReference = useCallback((sid, reference) => {
    if (activeStudentIdRef.current === sid) {
      setResourceReference(reference ? { ...reference, studentId: sid } : null)
      if (reference) setPdfReference(null)
    }
  }, [])

  const handlePdfReference = useCallback((sid, reference) => {
    if (activeStudentIdRef.current !== sid) return
    if (!reference) { setPdfReference(null); return }
    setPdfReference({ ...reference, studentId: sid })
    setResourceReference(null)
    setMode('chat')
  }, [])

  const handlePdfSource = useCallback((citation) => {
    setRightView('pdf')
    setPdfLocation({ ...citation, nonce: createId('source') })
    pdfWorkspaceRef.current?.openSource(citation)
  }, [])

  const handlePdfUpload = useCallback(async (file) => {
    if (!pdfWorkspaceRef.current) throw new Error('文档阅读区尚未就绪，请重试。')
    return pdfWorkspaceRef.current.upload(file)
  }, [])

  const handleArtifactSaved = useCallback((sid, resource) => {
    const next = updateSessionField(sid, 'resources', current => upsertArtifacts(current, [resource]))
    if (activeStudentIdRef.current === sid) {
      const index = next.findIndex(item => String(item.id) === String(resource.id))
      const saved = next[index] || resource
      setResourceReference(current => updatedArtifactReference(current, saved))
      if (index >= 0) updateSessionField(sid, 'activeResource', index)
    }
  }, [updateSessionField])

  const switchSession = useCallback(async (sid) => {
    persistSessionSnapshot(activeStudentIdRef.current)
    activeStudentIdRef.current = sid
    setResourceReference(null)
    setPdfReference(null)
    setPdfLocation(null)
    setMode('chat')
    const cached = sessionViewCacheRef.current.get(sid)
    setResources(cached?.resources || [])
    setProfile(cached?.profile || null)
    setLoading(Boolean(cached?.loading))
    setActiveResource(cached?.activeResource || 0)
    setStudentId(sid)
    window.localStorage.setItem('netmind_last_session_id', sid)
    setShowSidebar(false)
    try {
      const [resData, profileData] = await Promise.all([
        getResources(sid),
        fetchProfile(sid).catch(() => null),
      ])
      const latestCached = sessionViewCacheRef.current.get(sid) || {}
      const mergedResources = mergeSessionResources(resData, latestCached.resources || [])
      const nextState = {
        ...latestCached,
        resources: mergedResources,
        profile: profileData?.profile || latestCached.profile || null,
        activeResource: Math.min(
          latestCached.activeResource || 0,
          Math.max(mergedResources.length - 1, 0),
        ),
      }
      sessionViewCacheRef.current.set(sid, nextState)
      if (activeStudentIdRef.current === sid) {
        setResources(nextState.resources)
        setProfile(nextState.profile)
        setLoading(Boolean(nextState.loading))
        setActiveResource(nextState.activeResource)
      }
    } catch {
      if (activeStudentIdRef.current === sid && !cached) {
        setResources([])
        setProfile(null)
      }
    }
  }, [persistSessionSnapshot])

  useEffect(() => {
    let active = true
    const timer = setTimeout(async () => {
      const data = await loadSessions()
      if (!active) return
      const saved = window.localStorage.getItem('netmind_last_session_id')
      const target = data.some(session => session.student_id === saved)
        ? saved
        : data[0]?.student_id
      if (target) {
        await switchSession(target)
      } else {
        window.localStorage.setItem('netmind_last_session_id', initialSessionId.current)
      }
    }, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadSessions, switchSession])

  const newSession = useCallback(() => {
    persistSessionSnapshot(activeStudentIdRef.current)
    const newId = createId('session')
    activeStudentIdRef.current = newId
    setResourceReference(null)
    setPdfReference(null)
    setPdfLocation(null)
    sessionViewCacheRef.current.set(newId, {
      resources: [],
      profile: null,
      loading: false,
      activeResource: 0,
    })
    setStudentId(newId)
    window.localStorage.setItem('netmind_last_session_id', newId)
    setResources([])
    setProfile(null)
    setActiveResource(0)
    setMode('chat')
    setShowSidebar(false)
  }, [persistSessionSnapshot])

  const handleDeleteSession = useCallback(async (sid) => {
    if (!sid || !window.confirm('确定删除这个对话吗？该对话关联的资源、笔记和答题数据也会删除。')) return

    try {
      await deleteSession(sid, userId)
      const nextSession = sessions.find(session => session.student_id !== sid)
      sessionViewCacheRef.current.delete(sid)
      setSessions(current => current.filter(session => session.student_id !== sid))

      if (activeStudentIdRef.current === sid) {
        if (nextSession) {
          await switchSession(nextSession.student_id)
        } else {
          newSession()
        }
        sessionViewCacheRef.current.delete(sid)
      }
    } catch (error) {
      window.alert(`删除对话失败：${error.message}`)
    }
  }, [newSession, sessions, switchSession, userId])

  useEffect(() => {
    let lastInteractionAt = Date.now()
    const markActive = () => {
      lastInteractionAt = Date.now()
    }
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && Date.now() - lastInteractionAt < 120000) {
        recordLearningActivity(userId, studentId, 30).catch(() => {})
      }
    }, 30000)
    window.addEventListener('pointerdown', markActive)
    window.addEventListener('keydown', markActive)
    window.addEventListener('wheel', markActive, { passive: true })
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('pointerdown', markActive)
      window.removeEventListener('keydown', markActive)
      window.removeEventListener('wheel', markActive)
    }
  }, [studentId, userId])

  const handleTutorResourcesGenerated = useCallback((generatedResources, sid) => {
    if (!generatedResources?.length) return
    updateSessionField(sid, 'resources', current => upsertArtifacts(current, generatedResources))
    updateSessionField(sid, 'activeResource', 0)
    if (activeStudentIdRef.current === sid) {
      setMode('resource')
      setPdfReference(null)
      setRightView('resources')
    }
  }, [updateSessionField])

  useEffect(() => {
    const measure = () => {
      const main = mainRef.current
      const chatPanel = splitHandleRef.current?.previousElementSibling
      if (!main || !chatPanel) return
      const next = {
        viewportWidth: window.innerWidth,
        availableWidth: main.getBoundingClientRect().right - chatPanel.getBoundingClientRect().left,
      }
      setLayoutSize(current => (
        current.viewportWidth === next.viewportWidth && current.availableWidth === next.availableWidth
          ? current
          : next
      ))
    }
    const observer = new ResizeObserver(measure)
    observer.observe(mainRef.current)
    window.addEventListener('resize', measure)
    const frame = window.requestAnimationFrame(measure)
    return () => {
      window.cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [showSidebar])

  useEffect(() => () => resizeCleanupRef.current?.(), [])

  useEffect(() => {
    const handleContextMenu = (e) => {
      const selection = window.getSelection()
      const text = selection?.toString().trim()
      if (!text || text.length < 2) return

      const target = e.target
      if (target.closest('input, textarea, [contenteditable]')) return

      e.preventDefault()
      setContextMenu({ x: e.clientX, y: e.clientY, text })
    }
    document.addEventListener('contextmenu', handleContextMenu)
    return () => document.removeEventListener('contextmenu', handleContextMenu)
  }, [])

  const handleAddTextNote = useCallback(() => {
    if (!contextMenu) return
    const note = addTextNote(contextMenu.text, '页面摘录')
    createRemoteNote(userId, { ...note, studentId }).catch(() => {})
    recordLearningEvent(userId, {
      student_id: studentId,
      event_type: 'note_added',
      resource_type: 'text',
      title: '页面摘录',
      topic: contextMenu.text.slice(0, 60),
      metadata: {
        source: 'selection',
        content: contextMenu.text.slice(0, 240),
      },
    }).catch(() => {})
    setContextMenu(null)
  }, [contextMenu, studentId, userId])

  const handleResizePointerDown = useCallback((event) => {
    if (event.button !== 0 || window.innerWidth <= 760) return
    event.preventDefault()
    const handle = event.currentTarget
    const chatPanel = handle.previousElementSibling
    const main = handle.parentElement
    if (!chatPanel || !main) return

    const chatLeft = chatPanel.getBoundingClientRect().left
    const mainRight = main.getBoundingClientRect().right
    const layout = getChatLayout(window.innerWidth, mainRight - chatLeft, chatWidths)
    let lastWidth = layout.width
    let moved = false
    resizeCleanupRef.current?.()
    handle.setPointerCapture(event.pointerId)

    document.body.classList.add(styles.resizing)

    const onPointerMove = (moveEvent) => {
      lastWidth = clampChatWidth(moveEvent.clientX - chatLeft, layout.minWidth, layout.maxWidth)
      moved = true
      setChatWidths(current => ({ ...current, [layout.preferenceKey]: lastWidth }))
    }

    const onPointerUp = () => {
      document.body.classList.remove(styles.resizing)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerUp)
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId)
      if (moved) window.localStorage.setItem(CHAT_WIDTH_KEYS[layout.preferenceKey], String(lastWidth))
      resizeCleanupRef.current = null
    }

    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    window.addEventListener('pointercancel', onPointerUp)
    resizeCleanupRef.current = onPointerUp
  }, [chatWidths])

  const handleResizeKeyDown = (event) => {
    const offsets = { ArrowLeft: -16, ArrowRight: 16 }
    if (!(event.key in offsets) && event.key !== 'Home' && event.key !== 'End') return
    event.preventDefault()
    const requested = event.key === 'Home' ? chatLayout.minWidth
      : event.key === 'End' ? chatLayout.maxWidth : chatLayout.width + offsets[event.key]
    const width = clampChatWidth(requested, chatLayout.minWidth, chatLayout.maxWidth)
    setChatWidths(current => ({ ...current, [chatLayout.preferenceKey]: width }))
    window.localStorage.setItem(CHAT_WIDTH_KEYS[chatLayout.preferenceKey], String(width))
  }

  return (
    <div className={styles.appContainer}>
      <header className={styles.appHeader}>
        <div className={styles.headerLeft}>
          <button className={styles.headerBtn} onClick={() => setShowSidebar(!showSidebar)}>
            {showSidebar ? '关闭' : '历史'}
          </button>
          <button data-tour="notes-entry" className={styles.headerBtn} onClick={() => {
            if (!showNotes) setNotesKey(k => k + 1)
            setShowNotes(!showNotes)
          }}>
            {showNotes ? '关闭笔记' : '笔记'}
          </button>
        </div>
        <div className={styles.brandBlock}>
          <h1>NetMind Agent</h1>
          <span className={styles.headerSub}>持续探索，建立理解</span>
        </div>
        <div className={styles.headerActions}>
          <button data-tour="restart" className={styles.headerBtn} onClick={() => {
            prepareTourStep({})
            tourRef.current?.open()
          }}>新手引导</button>
          <button className={styles.headerBtn} onClick={() => setShowSettings(true)}>设置</button>
          <button data-tour="learning-record" className={styles.headerBtn} onClick={() => setShowUserSpace(true)}>学习记录</button>
          <button className={cx(styles.headerBtn, styles.newChatBtn)} onClick={newSession}>新对话</button>
        </div>
      </header>

      <main ref={mainRef} className={styles.appMain}>
        {showSidebar && (
          <aside className={styles.sessionSidebar} aria-label="历史对话">
            <div className={styles.sessionHeader}>
              <span>历史对话</span>
              <strong>{sessions.length}</strong>
            </div>
            {sessions.length === 0 && (
              <div className={styles.sessionEmpty}>暂无历史对话</div>
            )}
            {sessions.map(s => (
              <div key={s.student_id} className={styles.sessionRow}>
                <button
                  className={cx(styles.sessionItem, s.student_id === studentId && styles.active)}
                  onClick={() => switchSession(s.student_id)}
                >
                  <span className={styles.sessionPreview}>{s.last_message || '未命名会话'}</span>
                  <span className={styles.sessionMeta}>
                    {s.course && <span className={styles.sessionCourse}>{s.course}</span>}
                    <span className={styles.sessionCount}>{s.message_count} 条</span>
                  </span>
                </button>
                <button
                  type="button"
                  className={styles.deleteSessionButton}
                  aria-label="删除对话"
                  title="删除对话"
                  onClick={() => handleDeleteSession(s.student_id)}
                >
                  &times;
                </button>
              </div>
            ))}
          </aside>
        )}

        <ChatPanel
          tutorialRef={tutorialComposerRef}
          onTourEvent={name => tourRef.current?.event(name)}
          key={`chat-${studentId}`}
          studentId={studentId}
          userId={userId}
          resources={resources}
          resourceReference={resourceReference?.studentId === studentId ? resourceReference : null}
          pdfReference={pdfReference?.studentId === studentId ? pdfReference : null}
          onClearPdfReference={() => setPdfReference(null)}
          onOpenPdfSource={handlePdfSource}
          onPdfUpload={handlePdfUpload}
          onClearReference={() => handleResourceReference(studentId, null)}
          onClearSelection={() => setResourceReference(current => {
            if (!current) return current
            const { selection: _selection, ...reference } = current
            void _selection
            return reference
          })}
          onArtifactSaved={resource => handleArtifactSaved(studentId, resource)}
          style={{
            width: chatLayout.width,
            minWidth: chatLayout.width,
            maxWidth: chatLayout.width,
          }}
          mode={mode}
          setMode={nextMode => { setMode(nextMode); if (nextMode !== 'chat') setPdfReference(null) }}
          setResources={updater => updateSessionField(studentId, 'resources', updater)}
          setProfile={updater => updateSessionField(studentId, 'profile', updater)}
          loading={loading}
          setLoading={updater => updateSessionField(studentId, 'loading', updater)}
          setActiveResource={updater => updateSessionField(studentId, 'activeResource', updater)}
          onSessionCreated={loadSessions}
        />
        <div
          ref={splitHandleRef}
          className={styles.splitHandle}
          role="separator"
          aria-orientation="vertical"
          aria-label="调整对话区宽度"
          aria-valuemin={chatLayout.minWidth}
          aria-valuemax={chatLayout.maxWidth}
          aria-valuenow={Math.round(chatLayout.width)}
          tabIndex={0}
          title="拖拽调整对话区宽度"
          onPointerDown={handleResizePointerDown}
          onKeyDown={handleResizeKeyDown}
        />
        <section className={styles.rightWorkspace}>
          <nav className={styles.workspaceTabs} aria-label="右侧工作区">
            <button className={rightView === 'resources' ? styles.selectedTab : ''} onClick={() => { setRightView('resources'); setPdfReference(null) }}>学习资源</button>
            <button data-tour="document-tab" className={rightView === 'pdf' ? styles.selectedTab : ''} onClick={() => setRightView('pdf')}>文档原文</button>
          </nav>
          <div className={styles.workspacePane} hidden={rightView !== 'pdf'}>
            <PdfWorkspace key={`pdf-${studentId}`} ref={pdfWorkspaceRef} studentId={studentId} userId={userId} location={pdfLocation}
              onSelectReference={reference => handlePdfReference(studentId, reference)}
              onUploaded={() => { if (activeStudentIdRef.current === studentId) setRightView('pdf'); loadSessions() }} />
          </div>
          <div className={styles.workspacePane} hidden={rightView !== 'resources'}>
        <ResourcePanel
          key={`resources-${studentId}`}
          studentId={studentId}
          userId={userId}
          resources={resources}
          profile={profile}
          loading={loading}
          activeResource={activeResource}
          setActiveResource={updater => updateSessionField(studentId, 'activeResource', updater)}
          onTutorResourcesGenerated={items => handleTutorResourcesGenerated(items, studentId)}
          resourceReference={resourceReference?.studentId === studentId ? resourceReference : null}
          onReferenceChange={handleResourceReference}
          onArtifactSaved={handleArtifactSaved}
        />
          </div>
        </section>
      </main>
      {showUserSpace && (
        <UserSpace
          userId={userId}
          onClose={() => setShowUserSpace(false)}
          onOpenSession={(sid) => {
            setShowUserSpace(false)
            switchSession(sid)
          }}
        />
      )}
      {showNotes && (
        <NotesPanel key={notesKey} userId={userId} studentId={studentId} onClose={() => setShowNotes(false)} />
      )}
      {showSettings && (
        <SettingsPanel userId={userId} onClose={() => setShowSettings(false)} />
      )}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onAdd={handleAddTextNote}
          onClose={() => setContextMenu(null)}
        />
      )}
      <OnboardingTour ref={tourRef} onPrepare={prepareTourStep} onExample={text => tutorialComposerRef.current?.fillExample(text)} />
    </div>
  )
}

export default App
