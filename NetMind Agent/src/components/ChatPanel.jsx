import { artifactSelectionLabel } from '../utils/artifacts.mjs'
import { artifactEventReceiver } from '../utils/artifacts.mjs'
import { documentLocationLabel, documentKindLabel } from '../utils/documentReference.mjs'
import { useState, useRef, useEffect, useLayoutEffect, useImperativeHandle } from 'react'
import {
  sendChatMessageStream,
  sendResourceMessageStream,
  getConversations,
  uploadChatAttachments,
  deleteChatAttachment,
  saveMessageArtifact,
} from '../services/api'
import MarkdownContent from './MarkdownContent'
import UnderstandingCheck from './UnderstandingCheck'
import { cx } from '../utils/classNames'
import {
  applyStatusEvent,
  finalizeProgress,
  syncPlannedProgress,
  updateResourceProgress,
} from '../utils/resourceProgress'
import { failedResource, finishGeneration, generationOutcome, generationSummary } from '../utils/resourceGeneration.mjs'
import styles from './ChatPanel.module.scss'

const WELCOME_MESSAGE = '你好，我是 NetMind。告诉我你想弄懂什么。可以直接聊，生成带图解的讲解，或制作能调参数、看变化的互动教学内容；资料和笔记会留在这里，方便继续探索。'

const DEFAULT_PROGRESS = [
  { agent: 'profile', label: '理解问题', message: '结合当前问题与已有背景', status: 'pending' },
  { agent: 'knowledge', label: '查阅资料', message: '检索已选择的参考资料', status: 'pending' },
  { agent: 'lecture', label: '制作讲解', message: '组织文字、图解与过程演示', status: 'pending' },
]

const PROMPT_SUGGESTIONS = [
  { mode: 'resource', title: '用图解看懂原理', prompt: '为什么会出现月相变化？请用图解和逐步演示讲清楚。' },
  { mode: 'resource', title: '调参数，看变化', prompt: '生成一个互动网页：拖动电压和电阻，观察电流及电路变化，用它理解欧姆定律。' },
  { mode: 'chat', title: '从疑问接着聊', prompt: '我理解概念时容易只记住定义，怎样判断自己是否真的理解？' },
]

const RESOURCE_SHORTCUTS = [
  { intent: 'lecture', title: '多形式讲解' },
  { intent: 'interactive', title: '互动教学' },
  { intent: 'mindmap', title: '思维导图' },
  { intent: 'ppt', title: 'PPT课件' },
  { intent: 'lab', title: '实操案例' },
  { intent: 'quiz', title: '练习题' },
]

function resizeChatInput(textarea) {
  if (!textarea) return
  // Measure without the previous height or scrollbar affecting wrapped lines.
  textarea.style.overflowY = 'hidden'
  textarea.style.height = 'auto'
  const styles = getComputedStyle(textarea)
  const borders = parseFloat(styles.borderTopWidth) + parseFloat(styles.borderBottomWidth)
  textarea.style.height = `${textarea.scrollHeight + borders}px`
  textarea.style.overflowY = 'auto'
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatAttachmentError(value) {
  const raw = String(value || '').trim()
  if (!raw) return '文件解析失败，未返回具体原因。'
  try {
    const parsed = JSON.parse(raw)
    if (typeof parsed?.detail === 'string') return parsed.detail
    if (typeof parsed?.error === 'string') return parsed.error
  } catch {
    // The backend may already return a plain-text error.
  }
  return raw
}

function attachmentStatusLabel(attachment) {
  if (attachment.status === 'ready') return attachment.partial ? '部分解析' : '解析完成'
  if (attachment.status === 'processing') return '解析中'
  if (attachment.status === 'empty') return '未识别到文字'
  return '解析失败'
}

function makePlaceholderResource(item, status = 'pending') {
  return {
    id: `stream-${item.index ?? item.intent ?? Date.now()}`,
    streamIndex: item.index,
    intent: item.intent,
    type: item.type || item.intent || 'resource',
    title: item.title || `${item.label || '资源'}生成中`,
    content: '',
    metadata: {},
    label: item.label,
    status,
    isPlaceholder: true,
  }
}

export default function ChatPanel({
  tutorialRef,
  onTourEvent,
  studentId,
  userId,
  resources = [],
  resourceReference,
  pdfReference,
  onClearPdfReference,
  onOpenPdfSource,
  onPdfUpload,
  onClearReference,
  onClearSelection,
  onArtifactSaved,
  mode,
  setMode,
  setResources,
  setProfile,
  loading,
  setLoading,
  setActiveResource,
  onSessionCreated,
  style,
}) {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: WELCOME_MESSAGE },
  ])
  const [input, setInput] = useState('')
  const [progressSteps, setProgressSteps] = useState([])
  const [resourceDrafts, setResourceDrafts] = useState([])
  const [attachments, setAttachments] = useState([])
  const [selectedAttachment, setSelectedAttachment] = useState(null)
  const [pdfUploading, setPdfUploading] = useState(false)
  const [pdfUploadError, setPdfUploadError] = useState('')
  const [requiredIntents, setRequiredIntents] = useState([])
  const [isDraggingFiles, setIsDraggingFiles] = useState(false)
  const messagesRef = useRef(null)
  const fileInputRef = useRef(null)
  const chatInputRef = useRef(null)
  const dragDepthRef = useRef(0)
  const mountedRef = useRef(false)
  const historyRevisionRef = useRef(0)
  useImperativeHandle(tutorialRef, () => ({
    fillExample(text) {
      if (loading || pdfUploading) return { ok: false, reason: '当前正在处理请求，请完成后再试。' }
      if (input.trim() || attachments.length) return { ok: false, reason: '已保留你的草稿和附件；清空输入后才能填入示例。' }
      setInput(text)
      chatInputRef.current?.focus()
      return { ok: true }
    },
  }), [input, attachments.length, loading, pdfUploading])

  useLayoutEffect(() => {
    resizeChatInput(chatInputRef.current)
  }, [input])

  useLayoutEffect(() => {
    const textarea = chatInputRef.current
    let width = textarea.getBoundingClientRect().width
    const resize = () => resizeChatInput(textarea)
    const observer = new ResizeObserver(() => {
      const nextWidth = textarea.getBoundingClientRect().width
      // Height changes also notify observers; only remeasure for a new width.
      if (nextWidth === width) return
      width = nextWidth
      resize()
    })
    observer.observe(textarea, { box: 'border-box' })
    window.addEventListener('resize', resize)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', resize)
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    setRequiredIntents([])
    setResourceDrafts([])
    let active = true
    const timer = setTimeout(async () => {
      const revision = historyRevisionRef.current
      try {
        const convData = await getConversations(studentId, userId)
        if (!active || revision !== historyRevisionRef.current) return
        if (convData.length > 0) {
          setMessages(convData.map(c => ({ role: c.role, content: c.content, id: c.id ?? c.message_id, artifactAction: c.artifact_action, checkId: c.understanding_check_id, reference: c.resource_reference || null, pdfReference: c.pdf_reference, pdfCitations: c.pdf_citations })))
        } else {
          setMessages([{ role: 'assistant', content: WELCOME_MESSAGE }])
        }
      } catch {
        if (active && revision === historyRevisionRef.current) setMessages([{ role: 'assistant', content: WELCOME_MESSAGE }])
      }
    }, 0)

    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [studentId, userId])

  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
    }
  }, [messages, progressSteps])

  const resetProgress = () => {
    setProgressSteps([])
    setResourceDrafts([])
  }

  const handleSuggestion = (suggestion) => {
    setMode(suggestion.mode)
    setInput(suggestion.prompt)
  }

  const toggleRequiredIntent = (intent) => {
    setRequiredIntents(current => (
      current.includes(intent)
        ? current.filter(item => item !== intent)
        : [...current, intent]
    ))
    if (!requiredIntents.includes(intent)) onTourEvent?.('format-selected')
  }

  const uploadAttachments = async (filesLike) => {
    const allFiles = Array.from(filesLike || [])
    const pdfFiles = onPdfUpload ? allFiles.filter(file => /\.(pdf|docx|xlsx|xls|csv|md|markdown|txt)$/i.test(file.name)) : []
    const files = allFiles.filter(file => !pdfFiles.includes(file))
    if (pdfFiles.length) {
      setPdfUploading(true)
      setPdfUploadError('')
      try {
        for (const file of pdfFiles) await onPdfUpload(file)
      } catch (error) {
        if (mountedRef.current) setPdfUploadError(error.message || '文档上传失败')
      } finally {
        if (mountedRef.current) setPdfUploading(false)
      }
    }
    if (files.length === 0) return

    const pending = files.map((file, index) => ({
      id: `pending-${Date.now()}-${index}`,
      name: file.name,
      size_bytes: file.size,
      status: 'processing',
      progress: 2,
      stage: 'uploading',
      isImage: file.type.startsWith('image/'),
    }))
    setAttachments(current => [...current, ...pending])

    let processingProgress = 36
    let processingStarted = false
    const progressTimer = window.setInterval(() => {
      if (!processingStarted) return
      processingProgress = Math.min(92, processingProgress + Math.max(1, Math.round((92 - processingProgress) * 0.12)))
      const pendingIds = new Set(pending.map(item => item.id))
      setAttachments(current => current.map(item => (
        pendingIds.has(item.id)
          ? { ...item, progress: processingProgress, stage: 'processing' }
          : item
      )))
    }, 480)

    try {
      const result = await uploadChatAttachments(files, ({ progress, stage }) => {
        if (stage === 'processing') processingStarted = true
        const pendingIds = new Set(pending.map(item => item.id))
        setAttachments(current => current.map(item => (
          pendingIds.has(item.id)
            ? { ...item, progress, stage }
            : item
        )))
      })
      setAttachments(current => {
        const pendingIds = new Set(pending.map(item => item.id))
        return [
          ...current.filter(item => !pendingIds.has(item.id)),
          ...(result.attachments || []).map((item, index) => ({
            ...pending[index],
            ...item,
            progress: 100,
            stage: 'completed',
          })),
        ]
      })
    } catch (error) {
      setAttachments(current => current.map(item => (
        pending.some(pendingItem => pendingItem.id === item.id)
          ? { ...item, status: 'error', error: error.message || '文件解析失败' }
          : item
      )))
    } finally {
      window.clearInterval(progressTimer)
    }
  }

  const handleAttachmentSelect = (event) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    uploadAttachments(files)
  }

  const handleDragEnter = (event) => {
    if (!event.dataTransfer?.types?.includes('Files')) return
    event.preventDefault()
    dragDepthRef.current += 1
    setIsDraggingFiles(true)
  }

  const handleDragOver = (event) => {
    if (!event.dataTransfer?.types?.includes('Files')) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }

  const handleDragLeave = (event) => {
    if (!event.dataTransfer?.types?.includes('Files')) return
    event.preventDefault()
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
    if (dragDepthRef.current === 0) setIsDraggingFiles(false)
  }

  const handleDrop = (event) => {
    if (!event.dataTransfer?.types?.includes('Files')) return
    event.preventDefault()
    dragDepthRef.current = 0
    setIsDraggingFiles(false)
    if (event.dataTransfer.files?.length) {
      uploadAttachments(event.dataTransfer.files)
    }
  }

  const removeAttachment = async (attachment) => {
    setAttachments(current => current.filter(item => item.id !== attachment.id))
    setSelectedAttachment(current => current?.id === attachment.id ? null : current)
    if (
      attachment.status !== 'processing'
      && !attachment.id.startsWith('pending-')
      && !attachment.id.startsWith('error-')
    ) {
      deleteChatAttachment(attachment.id).catch(() => {})
    }
  }

  const clearSentAttachments = (sentAttachments) => {
    const sentIds = new Set(sentAttachments.map(item => item.id))
    setAttachments(current => current.filter(item => !sentIds.has(item.id)))
    sentAttachments.forEach(item => {
      deleteChatAttachment(item.id).catch(() => {})
    })
  }

  const handleSend = async (overrideText) => {
    const rawText = typeof overrideText === 'string' ? overrideText : input
    const readyAttachments = attachments.filter(item => item.status === 'ready')
    const text = rawText.trim() || (readyAttachments.length > 0 ? '请分析我上传的附件。' : '')
    if (!text || loading || pdfUploading || resourceReference?.pending_page || attachments.some(item => item.status === 'processing')) return
    const attachmentIds = readyAttachments.map(item => item.id)
    const requestedIntents = mode === 'resource' ? [...requiredIntents] : []
    const sentReference = resourceReference ? {
      resource_id: resourceReference.resource_id, version: resourceReference.version,
      ...(resourceReference.selection ? { selection: { ...resourceReference.selection } } : {}),
      ...(resourceReference.selection_source ? { selection_source: resourceReference.selection_source } : {}),
      ...(resourceReference.current_version != null ? { current_version: resourceReference.current_version } : {}),
    } : null
    const sentPdfReference = mode === 'chat' && pdfReference ? { ...pdfReference } : null
    historyRevisionRef.current += 1

    setInput('')
    setMessages(prev => [...prev, {
      role: 'user',
      content: text,
      attachments: readyAttachments.map(item => ({ name: item.name })),
      reference: resourceReference,
      pdfReference: sentPdfReference,
    }])
    setLoading(true)
    onTourEvent?.(mode === 'chat' ? 'chat-sent' : 'resource-sent')

    if (mode === 'chat') {
      const streamId = `chat-${Date.now()}`
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: '',
        isStreaming: true,
        streamId,
        streamStatus: '正在检索资料并整理学习上下文...',
      }])

      const updateStreamingMessage = (updater) => {
        if (!mountedRef.current) return
        setMessages(prev => prev.map(message => (
          message.streamId === streamId ? updater(message) : message
        )))
      }

      const applyArtifact = artifactEventReceiver(onArtifactSaved, action => {
        updateStreamingMessage(current => ({ ...current, artifactAction: action }))
      })
      try {
        await sendChatMessageStream(text, studentId, [], {
          onStatus: (payload) => {
            updateStreamingMessage(current => ({
              ...current,
              streamStatus: payload?.message || '正在组织回答...',
            }))
          },
          onProfile: (payload) => {
            if (payload) setProfile(payload)
          },
          onDelta: (payload) => {
            const delta = payload?.delta || ''
            if (!delta) return
            updateStreamingMessage(current => ({
              ...current,
              content: `${current.content || ''}${delta}`,
              streamStatus: '',
            }))
          },
          onContext: (payload) => {
            updateStreamingMessage(current => ({ ...current, context: payload }))
          },
          onArtifact: applyArtifact,
          onDone: (payload) => {
            applyArtifact(payload?.artifact)
            updateStreamingMessage(current => ({
              ...current,
              content: current.content || payload?.reply || '',
              id: payload?.conversation_id ?? payload?.message_id,
              checkId: payload?.understanding_check?.id || current.checkId,
              pdfCitations: payload?.pdf_citations,
              isStreaming: false,
              streamStatus: '',
            }))
          },
        }, userId, attachmentIds, sentReference, sentPdfReference)
        clearSentAttachments(readyAttachments)
        onSessionCreated?.()
      } catch (e) {
        updateStreamingMessage(current => ({
          ...current,
          content: current.content
            ? `${current.content}\n\n> 流式传输中断：${e.message}`
            : `请求失败：${e.message}`,
          isStreaming: false,
          streamStatus: '',
        }))
      } finally {
        setLoading(false)
      }
      return
    }

    resetProgress()
    setProgressSteps(DEFAULT_PROGRESS.map((step, index) => ({
      ...step,
      status: index === 0 ? 'current' : 'pending',
    })))

    const existingResources = Array.isArray(resources)
      ? resources.filter(resource => resource && !resource.isPlaceholder && resource.status !== 'pending' && resource.status !== 'generating')
      : []
    const generationOffset = existingResources.length
    let streamResources = [...existingResources]

    const getAbsoluteIndex = (relativeIndex) => {
      const nextRelativeIndex = Number.isInteger(relativeIndex)
        ? relativeIndex
        : Math.max(streamResources.length - generationOffset, 0)
      return generationOffset + nextRelativeIndex
    }

    const commitStreamResources = (nextResources, activeIndex = null) => {
      streamResources = nextResources
      setResourceDrafts(streamResources.slice(generationOffset))
      setResources([...streamResources])
      if (activeIndex !== null) {
        setActiveResource(Math.max(activeIndex, 0))
      }
    }

    try {
      setResources([...existingResources])
      await sendResourceMessageStream(text, studentId, {
        onStatus: (payload) => {
          setProgressSteps(prev => applyStatusEvent(prev, payload, DEFAULT_PROGRESS))
        },
        onProfile: (payload) => {
          setProfile(payload)
        },
        onPlan: (payload) => {
          const planned = (payload.resources || []).map(item => makePlaceholderResource(item))
          setProgressSteps(prev => syncPlannedProgress(prev, payload.resources || []))
          if (planned.length > 0) {
            commitStreamResources(
              [...existingResources, ...planned],
              existingResources.length === 0 ? 0 : null,
            )
          }
        },
        onNotice: (payload) => {
          if (payload.message) {
            setMessages(prev => [...prev, { role: 'assistant', content: payload.message, isSystem: true }])
          }
        },
        onResourceStart: (payload) => {
          setProgressSteps(prev => updateResourceProgress(prev, payload, 'current'))
          const index = getAbsoluteIndex(payload.index)
          const next = [...streamResources]
          next[index] = {
            ...(next[index] || makePlaceholderResource(payload)),
            ...makePlaceholderResource(payload, 'generating'),
          }
          commitStreamResources(next)
        },
        onResourceProgress: (payload) => {
          setProgressSteps(prev => updateResourceProgress(
            prev,
            payload,
            'current',
            `${payload.stage || '正在生成'} · ${payload.progress || 0}%`,
          ))
          const index = getAbsoluteIndex(payload.index)
          const next = [...streamResources]
          const resource = next[index] || makePlaceholderResource(payload, 'generating')
          next[index] = {
            ...resource,
            status: 'generating',
            metadata: {
              ...(resource.metadata || {}),
              generation_progress: {
                progress: payload.progress || 0,
                stage: payload.stage || '正在生成',
                message: payload.message || '',
              },
            },
          }
          commitStreamResources(next)
        },
        onResourceDelta: (payload) => {
          const index = getAbsoluteIndex(payload.index)
          const current = streamResources[index] || makePlaceholderResource(payload, 'generating')
          const next = [...streamResources]
          next[index] = {
            ...current,
            status: 'generating',
            isPlaceholder: false,
            content: `${current.content || ''}${payload.delta || ''}`,
          }
          commitStreamResources(next)
        },
        onResource: (payload) => {
          const resource = payload.resource || payload
          setProgressSteps(prev => updateResourceProgress(prev, payload, 'done'))
          const index = getAbsoluteIndex(payload.index)
          const next = [...streamResources]
          next[index] = {
            ...resource,
            status: 'ready',
            isPlaceholder: false,
          }
          commitStreamResources(next)
        },
        onResourceError: (payload) => {
          setProgressSteps(prev => updateResourceProgress(prev, payload, 'error'))
          const index = getAbsoluteIndex(payload.index)
          const next = [...streamResources]
          next[index] = failedResource(next[index] || makePlaceholderResource(payload), payload)
          commitStreamResources(next)
        },
        onDone: (payload) => {
          commitStreamResources(finishGeneration(streamResources, generationOffset))
          setProgressSteps(prev => finalizeProgress(prev))
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: payload?.understanding_check ? (payload.reply || payload.summary || '请用自己的话回答下面的问题。') : generationSummary(streamResources, generationOffset, payload),
            checkId: payload?.understanding_check?.id,
            isSystem: true,
          }])
        },
      }, userId, attachmentIds, requestedIntents, sentReference)

      clearSentAttachments(readyAttachments)
      onSessionCreated?.()
    } catch (streamError) {
      commitStreamResources(finishGeneration(streamResources, generationOffset, {
        message: '生成连接中断。可以从历史对话重新载入已保存的资源。',
      }))
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `生成连接中断：${streamError.message}。已收到的内容保留；请从历史对话重新载入确认结果，再决定是否重试。`,
      }])
      onSessionCreated?.()
    } finally {
      if (generationOutcome(streamResources, generationOffset).succeeded > 0) setRequiredIntents([])
      setLoading(false)
      setTimeout(resetProgress, 1200)
    }
  }

  const handleSaveMessage = async (message) => {
    if (message.id == null || message.saving) return
    const update = (fields) => {
      if (!mountedRef.current) return
      setMessages(current => current.map(item => item === message || item.id === message.id ? { ...item, ...fields } : item))
    }
    update({ saving: true, saveError: '' })
    try {
      const artifact = await saveMessageArtifact(studentId, userId, message.id)
      onArtifactSaved(artifact)
      update({ saving: false, savedArtifact: artifact })
    } catch (error) {
      update({ saving: false, saveError: `保存作品失败：${error.message || '请重试。'}` })
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const showSuggestions = !loading && messages.length <= 1
  const attachmentsProcessing = attachments.some(item => item.status === 'processing')
  const canSend = (Boolean(input.trim()) || attachments.some(item => item.status === 'ready'))
    && !resourceReference?.pending_page
    && !loading
    && !attachmentsProcessing

  return (
    <div
      className={cx(styles.chatPanel, isDraggingFiles && styles.dragActive)}
      style={style}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isDraggingFiles && (
        <div className={styles.dropOverlay} aria-hidden="true">
          <span>DROP FILES</span>
          <strong>松开以上传并解析</strong>
        </div>
      )}
      {selectedAttachment && (
        <div
          className={styles.attachmentDetailBackdrop}
          role="presentation"
          onMouseDown={() => setSelectedAttachment(null)}
        >
          <section
            className={styles.attachmentDetail}
            role="dialog"
            aria-modal="true"
            aria-label="附件解析详情"
            onMouseDown={event => event.stopPropagation()}
          >
            <header>
              <div>
                <span>ATTACHMENT DETAIL</span>
                <h3>{selectedAttachment.name}</h3>
              </div>
              <button
                type="button"
                onClick={() => setSelectedAttachment(null)}
                aria-label="关闭附件详情"
                title="关闭"
              >
                ×
              </button>
            </header>
            <div className={styles.attachmentDetailMeta}>
              <span className={styles[selectedAttachment.status]}>
                {attachmentStatusLabel(selectedAttachment)}
              </span>
              <span>{formatBytes(selectedAttachment.size_bytes)}</span>
              {selectedAttachment.extension && <span>{selectedAttachment.extension.toUpperCase()}</span>}
              {selectedAttachment.parser && <span>{selectedAttachment.parser}</span>}
            </div>
            {selectedAttachment.status === 'error' ? (
              <div className={styles.attachmentErrorDetail}>
                <span>FAILURE REASON</span>
                <p>{formatAttachmentError(selectedAttachment.error)}</p>
              </div>
            ) : (
              <div className={styles.attachmentPreviewDetail}>
                <span>PARSE RESULT</span>
                {selectedAttachment.warning && (
                  <p className={styles.attachmentWarning}>{selectedAttachment.warning}</p>
                )}
                <p>
                  {selectedAttachment.status === 'processing'
                    ? `当前进度 ${selectedAttachment.progress || 0}%`
                    : selectedAttachment.preview || '暂无可预览文本。'}
                </p>
              </div>
            )}
          </section>
        </div>
      )}
      <div className={styles.chatModeBar} role="tablist" aria-label="对话模式">
        <button
          className={cx(styles.modeBtn, mode === 'chat' && styles.active)}
          onClick={() => setMode('chat')}
          aria-pressed={mode === 'chat'}
        >
          对话
        </button>
        <button
          data-tour="resource-mode"
          className={cx(styles.modeBtn, mode === 'resource' && styles.active)}
          onClick={() => setMode('resource')}
          aria-pressed={mode === 'resource'}
        >
          生成资源
        </button>
      </div>

      <div className={styles.chatMessages} ref={messagesRef}>
        {messages.map((msg, i) => (
          <div key={i} className={cx(styles.message, styles[msg.role], msg.isSystem && styles.system)}>
            {msg.role === 'assistant' && <div className={styles.msgLabel}>NetMind</div>}
            {msg.role === 'assistant' ? (
              msg.isStreaming && !msg.content ? (
                <div className={styles.streamStatus}>
                  <div className={styles.loadingDots}><span></span><span></span><span></span></div>
                  <span className={styles.statusText}>{msg.streamStatus || '正在组织回答...'}</span>
                </div>
              ) : (
                <div className={styles.streamingReply}>
                  <MarkdownContent content={msg.content} className={styles.chatMarkdown} pdfCitations={msg.pdfCitations} onCitationClick={onOpenPdfSource} />
                  {msg.isStreaming && <span className={styles.streamCursor} aria-hidden="true"></span>}
                </div>
              )
            ) : (
              <>
                <div>{msg.content}</div>
                {msg.reference && <small className={styles.messageReference}>引用：{msg.reference.title} · v{msg.reference.version}{msg.reference.selection ? ` · ${artifactSelectionLabel(msg.reference.selection)}` : ''}</small>}
                {msg.pdfReference && <button type="button" className={styles.pdfSourceLink} onClick={() => onOpenPdfSource?.(msg.pdfReference)} title={msg.pdfReference.text}>引用：{msg.pdfReference.title} · {documentLocationLabel(msg.pdfReference)} ↗</button>}
                {msg.attachments?.length > 0 && (
                  <div className={styles.messageAttachments}>
                    {msg.attachments.map(item => <span key={item.name}>{item.name}</span>)}
                  </div>
                )}
              </>
            )}
            {msg.checkId && <UnderstandingCheck key={`${studentId}-${msg.checkId}`} checkId={msg.checkId} studentId={studentId} userId={userId} />}
            {msg.role === 'assistant' && msg.pdfCitations?.length > 0 && <div className={styles.pdfSources} aria-label="回答出处">{msg.pdfCitations.map(source => <button key={source.id} type="button" onClick={() => onOpenPdfSource?.(source)} title={source.text}>原文 · {source.title} · {documentLocationLabel(source)} ↗</button>)}</div>}
            {msg.role === 'assistant' && !msg.isStreaming && !msg.isSystem && !msg.artifactAction && !msg.checkId && !msg.pdfCitations?.length && msg.id != null && <div className={styles.messageActions}>
              <button type="button" disabled={msg.saving || Boolean(msg.savedArtifact)} onClick={() => handleSaveMessage(msg)}>
                {msg.saving ? '正在保存…' : msg.savedArtifact ? `已保存为作品 · v${msg.savedArtifact.version || 1}` : '保存为作品'}
              </button>
              {msg.saveError && <span role="alert">{msg.saveError}</span>}
            </div>}
            {msg.context?.references?.length > 0 && <small className={styles.messageReference}>
              已引用：{msg.context.references.map(item => `${item.title || '作品'} · v${item.version}`).join('、')}
            </small>}
          </div>
        ))}

        {showSuggestions && (
          <div className={styles.promptStarters} aria-label="示例问题">
            {PROMPT_SUGGESTIONS.map(suggestion => (
              <button
                key={suggestion.title}
                className={styles.promptCard}
                onClick={() => handleSuggestion(suggestion)}
              >
                <span>{suggestion.title}</span>
                <strong>{suggestion.prompt}</strong>
              </button>
            ))}
          </div>
        )}

        {loading && mode === 'resource' && (
          <div className={cx(styles.message, styles.assistant, styles.progressMessage)}>
            <div className={styles.msgLabel}>NetMind</div>
            <div className={styles.agentProgress}>
              {(progressSteps.length > 0 ? progressSteps : DEFAULT_PROGRESS).map(step => (
                <div key={step.key || `${step.agent}-${step.label}`} className={cx(styles.agentStep, styles[step.status])}>
                  <span className={styles.agentIcon}>{step.label.slice(0, 2)}</span>
                  <span className={styles.agentLabel}>
                    <strong>{step.label}</strong>
                    <small>{step.message}</small>
                  </span>
                  {step.status === 'done' && <span className={styles.agentCheck}>完成</span>}
                  {step.status === 'error' && <span className={styles.agentError}>失败</span>}
                  {step.status === 'current' && (
                    <span className={styles.agentLoading}>
                      <span className={styles.dot}></span><span className={styles.dot}></span><span className={styles.dot}></span>
                    </span>
                  )}
                </div>
              ))}
              {resourceDrafts.length > 0 && (
                <div className={styles.resourceDraftNote}>
                  已完成 {resourceDrafts.filter(r => r.status === 'ready').length} / {resourceDrafts.length} 个资源
                </div>
              )}
            </div>
          </div>
        )}

      </div>

      {mode === 'resource' && !loading && (
        <div data-tour="resource-formats" className={styles.resourceShortcuts} aria-label="指定本次必须生成的资源">
          <span className={styles.resourceShortcutLabel}>
            {requiredIntents.length > 0 ? '必定生成' : '自动推测'}
          </span>
          {RESOURCE_SHORTCUTS.map(shortcut => (
            <button
              key={shortcut.intent}
              type="button"
              className={cx(
                styles.resourceShortcut,
                requiredIntents.includes(shortcut.intent) && styles.selected,
              )}
              onClick={() => toggleRequiredIntent(shortcut.intent)}
              aria-pressed={requiredIntents.includes(shortcut.intent)}
            >
              {shortcut.title}
            </button>
          ))}
        </div>
      )}

      <div className={styles.composer}>
        {pdfUploading && <div className={styles.pdfUploadNotice} role="status">正在打开文档原文…</div>}
        {pdfUploadError && <div className={styles.pdfUploadNotice} role="alert">{pdfUploadError}</div>}
        {pdfReference ? <div data-tour="chat-reference" className={styles.referenceBar} aria-label="当前文档引用" role="status">
          <div className={styles.referenceHeading}>
            <span className={styles.referenceLabel}>{documentKindLabel(pdfReference)}</span>
            <strong title={pdfReference.title}>{pdfReference.title}</strong>
            <button className={styles.referenceDismiss} type="button" onClick={onClearPdfReference} aria-label="取消文档引用">×</button>
          </div>
          <div className={styles.referenceDetails}><span>{documentLocationLabel(pdfReference)} · 已选原文</span><button type="button" className={styles.referenceClear} onClick={() => onOpenPdfSource?.(pdfReference)}>定位原文 ↗</button></div>
          <span className={styles.selectionExcerpt}>{pdfReference.text}</span>
        </div> :
        <div data-tour="chat-reference" className={cx(styles.referenceBar, !resourceReference && styles.referenceEmpty, resourceReference?.reference_error && styles.referenceError)} aria-label="当前作品引用" role="status">
          {resourceReference ? <>
            <div className={styles.referenceHeading}>
              <span className={styles.referenceLabel}>引用</span>
              <strong title={resourceReference.title}>{resourceReference.title}</strong>
              <button className={styles.referenceDismiss} type="button" onClick={onClearReference} aria-label="取消作品引用" title="取消作品引用">×</button>
            </div>
            <div className={styles.referenceDetails}>
              <span className={styles.referenceVersion}>v{resourceReference.version}</span>
              {resourceReference.pending_page ? <span className={styles.referenceProgress}>{resourceReference.reference_error ? '页面引用失败，可重试或取消引用' : `正在读取第${resourceReference.pending_page}页…`}</span> : resourceReference.selection ? <span className={styles.referenceScope}>{resourceReference.selection.slide_number ? artifactSelectionLabel(resourceReference.selection) : '所选段落'}</span> : <span className={styles.referenceScope}>整份作品</span>}
              {resourceReference.selection && <button className={styles.referenceClear} type="button" onClick={onClearSelection}>{resourceReference.selection.slide_number ? '清除页面' : '清除段落'}</button>}
            </div>
            {resourceReference.selection && !resourceReference.selection.slide_number && !resourceReference.pending_page && <span className={styles.selectionExcerpt}>{artifactSelectionLabel(resourceReference.selection)}</span>}
          </> : <><span className={styles.referenceLabel}>未引用</span><span>在右侧选择作品或文档原文选段</span></>}
        </div>}
        {attachments.length > 0 && (
          <div className={styles.attachmentList} aria-label="待发送附件">
            {attachments.map(attachment => (
              <div
                key={attachment.id}
                className={cx(styles.attachmentItem, styles[attachment.status])}
                role="button"
                tabIndex={0}
                aria-label={`查看 ${attachment.name} 解析详情`}
                onClick={() => setSelectedAttachment(attachment)}
                onKeyDown={event => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    setSelectedAttachment(attachment)
                  }
                }}
              >
                <span className={styles.attachmentName}>{attachment.name}</span>
                <small>
                  {attachment.status === 'processing' && attachment.stage === 'uploading' && `上传中 ${attachment.progress || 0}%`}
                  {attachment.status === 'processing' && attachment.stage !== 'uploading' && `${attachment.isImage ? 'OCR 识别中' : '文档解析中'} ${attachment.progress || 36}%`}
                  {attachment.status === 'ready' && (
                    `${attachment.characters || 0} 字${attachment.partial ? ' · 部分识别' : ''}`
                  )}
                  {attachment.status === 'empty' && '未识别到文字'}
                  {attachment.status === 'error' && '解析失败 · 点击查看'}
                </small>
                {attachment.status === 'processing' && (
                  <span className={styles.attachmentProgress} aria-hidden="true">
                    <span style={{ width: `${attachment.progress || 0}%` }}></span>
                  </span>
                )}
                <button
                  type="button"
                  className={styles.removeAttachment}
                  onClick={event => {
                    event.stopPropagation()
                    removeAttachment(attachment)
                  }}
                  aria-label={`移除 ${attachment.name}`}
                  title="移除附件"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <div data-tour="composer" className={styles.chatInputArea}>
          <input
            ref={fileInputRef}
            className={styles.fileInput}
            type="file"
            multiple
            accept=".txt,.md,.markdown,.csv,.json,.docx,.xlsx,.xls,.ppt,.pptx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff"
            onChange={handleAttachmentSelect}
          />
          <button
            type="button"
            className={styles.attachButton}
            onClick={() => fileInputRef.current?.click()}
            disabled={loading}
            aria-label="上传图片或文件"
            title="上传图片或文件"
          >
            +
          </button>
          <textarea
            ref={chatInputRef}
            rows={1}
            aria-label="学习问题"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
          />
          <button type="button" className={styles.sendButton} onClick={() => handleSend()} disabled={!canSend}>
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
