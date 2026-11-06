import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { getInteractiveTitle, interactiveFilename, INTERACTIVE_PERMISSIONS, INTERACTIVE_SANDBOX, isInteractiveEscapeMessage, isInteractiveResizeMessage, prepareInteractiveHtml } from '../utils/interactiveHtml.mjs'
import styles from './InteractiveHtmlViewer.module.scss'

function ActionIcon({ action }) {
  const paths = {
    reset: <path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7" />,
    download: <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />,
    expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
  }
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[action]}</svg>
}

function InteractiveFrame({ resource, prepared }) {
  const [restart, setRestart] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [modalRestart, setModalRestart] = useState(0)
  const [frameHeight, setFrameHeight] = useState(480)
  const [modalFrameHeight, setModalFrameHeight] = useState(480)
  const frameRef = useRef(null)
  const modalRef = useRef(null)
  const modalCloseRef = useRef(null)
  const modalFrameRef = useRef(null)
  const previewButtonRef = useRef(null)
  const modalTitleId = useId()
  const urlsRef = useRef(new Set())
  const timersRef = useRef(new Set())
  const title = getInteractiveTitle(resource.title)
  const features = (Array.isArray(resource.metadata?.interactive_features) ? resource.metadata.interactive_features : [])
    .filter(item => typeof item === 'string' && item.trim()).map(item => item.trim().slice(0, 180)).slice(0, 10)

  useEffect(() => {
    const urls = urlsRef.current
    const timers = timersRef.current
    return () => {
      timers.forEach(timer => clearTimeout(timer)); timers.clear()
      urls.forEach(url => URL.revokeObjectURL(url)); urls.clear()
    }
  }, [])

  useEffect(() => {
    if (!modalOpen) return
    const dialog = modalRef.current
    const opener = previewButtonRef.current
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    if (!dialog.open) dialog.showModal()
    modalCloseRef.current?.focus()
    return () => {
      if (dialog.open) dialog.close()
      document.body.style.overflow = originalOverflow
      if (opener?.isConnected) opener.focus()
    }
  }, [modalOpen])

  useEffect(() => {
    const receiveFromFrame = event => {
      if (isInteractiveResizeMessage(event.data)) {
        if (event.source === frameRef.current?.contentWindow) setFrameHeight(event.data.height)
        else if (event.source === modalFrameRef.current?.contentWindow) setModalFrameHeight(event.data.height)
      } else if (event.source === modalFrameRef.current?.contentWindow && isInteractiveEscapeMessage(event.data)) {
        setModalOpen(false)
      }
    }
    window.addEventListener('message', receiveFromFrame)
    return () => window.removeEventListener('message', receiveFromFrame)
  }, [])

  function createFileUrl() {
    const url = URL.createObjectURL(new Blob([prepared.standalone], { type: 'text/html;charset=utf-8' }))
    urlsRef.current.add(url)
    return url
  }

  function releaseLater(url) {
    // Keep the blob alive while the download starts loading.
    const timer = setTimeout(() => {
      URL.revokeObjectURL(url); urlsRef.current.delete(url); timersRef.current.delete(timer)
    }, 60000)
    timersRef.current.add(timer)
  }

  function reset() {
    setLoaded(false); setFrameHeight(480); setRestart(value => value + 1); setError('')
    setNotice('已重新开始，页面参数和操作恢复初始状态。')
  }

  function download() {
    let url
    try {
      setError(''); url = createFileUrl()
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = interactiveFilename(title); anchor.rel = 'noopener noreferrer'
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
      releaseLater(url); setNotice('HTML 文件已准备下载，保存后可独立打开操作。')
    } catch (cause) {
      if (url) { URL.revokeObjectURL(url); urlsRef.current.delete(url) }
      setError(`HTML 下载失败：${cause.message || '浏览器无法创建文件'}。`)
    }
  }

  function openPreview() {
    setError(''); setModalFrameHeight(480); setModalRestart(0); setModalOpen(true)
  }

  function resetModal() {
    setModalFrameHeight(480); setModalRestart(value => value + 1)
  }

  return <div className={styles.viewer}>
    <div className={styles.toolbar}><div className={styles.heading}><span>INTERACTIVE WEB</span><strong title={title}>{title}</strong><small>调整参数，观察结果；支持离线使用。</small></div><div className={styles.actions}><button type="button" onClick={reset}><ActionIcon action="reset" />重新开始</button><button type="button" onClick={download} className={styles.primary}><ActionIcon action="download" />下载保存</button><button ref={previewButtonRef} type="button" onClick={openPreview}><ActionIcon action="expand" />全屏预览</button></div></div>
    <div className={styles.localNote}><i aria-hidden="true" /><span>在当前设备运行，操作控件即可继续探索。</span><small role="status" aria-live="polite">{loaded ? '画面已载入' : '正在载入画面…'}</small></div>
    <div className={styles.frameShell}><iframe ref={frameRef} key={restart} style={{ height: frameHeight }} title={`${title} · 互动教学预览`} srcDoc={prepared.preview} sandbox={INTERACTIVE_SANDBOX} allow={INTERACTIVE_PERMISSIONS} referrerPolicy="no-referrer" onLoad={() => setLoaded(true)} /></div>
    {features.length > 0 && <div className={styles.features}><span>本页设计的交互</span><ul>{features.map((feature, index) => <li key={index}>{feature}</li>)}</ul></div>}
    {notice && <p className={styles.notice} role="status">{notice}</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    <dialog ref={modalRef} className={styles.fullscreenDialog} aria-modal="true" aria-labelledby={modalTitleId} onCancel={event => { event.preventDefault(); setModalOpen(false) }} onClose={() => setModalOpen(false)}>
      <div className={styles.dialogContents}>
        <header className={styles.dialogHeader}><div><span>INTERACTIVE WEB</span><h2 id={modalTitleId}>{title}</h2></div><div className={styles.actions}><button type="button" onClick={resetModal}><ActionIcon action="reset" />重新开始</button><button type="button" onClick={download}><ActionIcon action="download" />下载保存</button><button ref={modalCloseRef} type="button" onClick={() => setModalOpen(false)} aria-label="关闭全屏预览"><ActionIcon action="close" />关闭预览</button></div></header>
        {modalOpen && <iframe ref={modalFrameRef} key={modalRestart} style={{ height: modalFrameHeight }} title={`${title} · 全屏互动预览`} srcDoc={prepared.preview} sandbox={INTERACTIVE_SANDBOX} allow={INTERACTIVE_PERMISSIONS} referrerPolicy="no-referrer" />}
        {error && <p className={styles.dialogError} role="alert">{error}</p>}
      </div>
    </dialog>
  </div>
}

export default function InteractiveHtmlViewer({ resource }) {
  const prepared = useMemo(() => prepareInteractiveHtml(resource?.content, getInteractiveTitle(resource?.title)), [resource?.content, resource?.title])
  if (!resource) return null
  if (prepared.error) return <div className={styles.invalid} role="alert"><span>互动教学暂不可用</span><strong>{resource.title || '网页生成未完成'}</strong><p>{prepared.error}</p></div>
  return <InteractiveFrame key={prepared.preview} resource={resource} prepared={prepared} />
}
