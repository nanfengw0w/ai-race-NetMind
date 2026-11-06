import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import MarkdownContent from './MarkdownContent'
import { clampStudio, hitExplanationNode, layoutExplanation, locateExplanationStep, parseExplanationBundle, placeExplanationLabel, shouldLabelExplanationEdge, selectStudioCodec, studioTime } from './explanationStudioUtils.mjs'
import styles from './ExplanationStudio.module.scss'

const PALETTE = { paper: '#fbfbfa', card: '#ffffff', ink: '#1a1a1a', muted: '#6b6b6b', line: '#e0e0e0', accent: '#b23a2f', soft: '#f8eeeb' }
const SANS = '"Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif'
const SERIF = '"Noto Serif SC", "Source Han Serif SC", SimSun, serif'

function Icon({ type, size = 15 }) {
  const paths = {
    play: <path d="m8 5 11 7-11 7Z" />,
    pause: <><path d="M8 5v14M16 5v14" /></>,
    reset: <><path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7" /></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" /></>,
    film: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4m-4 6h4m10-6h4m-4 6h4" /></>,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    previous: <path d="m14 6-6 6 6 6" />,
    next: <path d="m10 6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[type] || paths.play}</svg>
}

function roundBox(ctx, x, y, width, height, radius, fill, stroke) {
  ctx.beginPath()
  if (ctx.roundRect) ctx.roundRect(x, y, width, height, radius)
  else {
    ctx.moveTo(x + radius, y); ctx.arcTo(x + width, y, x + width, y + height, radius)
    ctx.arcTo(x + width, y + height, x, y + height, radius); ctx.arcTo(x, y + height, x, y, radius)
    ctx.arcTo(x, y, x + width, y, radius); ctx.closePath()
  }
  if (fill) { ctx.fillStyle = fill; ctx.fill() }
  if (stroke) { ctx.strokeStyle = stroke; ctx.stroke() }
}

function textLines(ctx, value, width) {
  const result = []
  for (const paragraph of String(value || '').split('\n')) {
    let line = ''
    for (const char of paragraph) {
      if (line && ctx.measureText(line + char).width > width) { result.push(line); line = char }
      else line += char
    }
    result.push(line)
  }
  return result
}

function paintText(ctx, value, x, y, width, { size = 22, color = PALETTE.ink, family = SANS, weight = 400, lineHeight = 30, limit = 2 } = {}) {
  ctx.font = `${weight} ${size}px ${family}`; ctx.fillStyle = color
  const lines = textLines(ctx, value, width)
  lines.slice(0, limit).forEach((line, index) => ctx.fillText(index === limit - 1 && lines.length > limit ? `${line.slice(0, -1)}…` : line, x, y + index * lineHeight))
}

function connection(from, to) {
  const centerA = { x: from.x + from.width / 2, y: from.y + from.height / 2 }
  const centerB = { x: to.x + to.width / 2, y: to.y + to.height / 2 }
  const dx = centerB.x - centerA.x, dy = centerB.y - centerA.y
  const ratioA = 1 / Math.max(Math.abs(dx) / (from.width / 2), Math.abs(dy) / (from.height / 2), 1)
  const ratioB = 1 / Math.max(Math.abs(dx) / (to.width / 2), Math.abs(dy) / (to.height / 2), 1)
  return { x1: centerA.x + dx * ratioA, y1: centerA.y + dy * ratioA, x2: centerB.x - dx * ratioB, y2: centerB.y - dy * ratioB }
}

/** One controlled renderer is used for the visible scene and actual recording. */
function drawStudioFrame(canvas, bundle, positioned, time, mode, selectedId) {
  const ctx = canvas?.getContext('2d')
  if (!ctx) return
  const { step, index, progress } = locateExplanationStep(bundle, time)
  const process = mode === 'process' && Boolean(step)
  const activeNodes = new Set(process ? step.active_nodes : [])
  const activeEdges = new Set(process ? step.active_edges : [])
  const byId = new Map(positioned.map(node => [node.id, node]))
  const occupiedLabels = []
  ctx.save(); ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.scale(canvas.width / 1280, canvas.height / 720)
  ctx.fillStyle = PALETTE.paper; ctx.fillRect(0, 0, 1280, 720)
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'
  paintText(ctx, 'NETMIND  /  EXPLANATION', 58, 38, 1080, { size: 13, color: PALETTE.muted, limit: 1 })
  paintText(ctx, bundle.title || '概念图解', 58, 89, 1164, { size: 32, family: SERIF, weight: 600, limit: 1 })
  ctx.strokeStyle = PALETTE.line; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(58, 115); ctx.lineTo(1222, 115); ctx.stroke()
  paintText(ctx, process ? `${String(index + 1).padStart(2, '0')}  ${step.title}` : '概念与关系', 66, 162, 1148, { size: 20, color: PALETTE.accent, limit: 1 })

  bundle.edges.forEach((edge, edgeIndex) => {
    const from = byId.get(edge.from), to = byId.get(edge.to)
    if (!from || !to) return
    const active = activeEdges.has(edgeIndex)
    ctx.strokeStyle = active ? PALETTE.accent : '#bfb9b1'; ctx.lineWidth = active ? 3 : 1.5
    let midX, midY
    ctx.beginPath()
    if (from.id === to.id) {
      const x = from.x + from.width - 18, y = from.y
      ctx.moveTo(x - 33, y); ctx.bezierCurveTo(x - 33, y - 37, x + 20, y - 37, x + 20, y + 15)
      midX = x - 5; midY = y - 28
      ctx.stroke(); ctx.beginPath(); ctx.moveTo(x + 15, y + 9); ctx.lineTo(x + 20, y + 16); ctx.lineTo(x + 25, y + 9); ctx.stroke()
    } else {
      const { x1, y1, x2, y2 } = connection(from, to)
      // Opposite-direction relationships get separate curves, rather than overlap.
      const reverse = bundle.edges.some(other => other.from === edge.to && other.to === edge.from)
      const bend = reverse ? 21 : 0
      const dx = x2 - x1, dy = y2 - y1, length = Math.max(1, Math.hypot(dx, dy))
      midX = (x1 + x2) / 2 - dy / length * bend; midY = (y1 + y2) / 2 + dx / length * bend
      ctx.moveTo(x1, y1); ctx.quadraticCurveTo(midX, midY, x2, y2); ctx.stroke()
      const angle = Math.atan2(y2 - midY, x2 - midX)
      ctx.beginPath(); ctx.moveTo(x2 - 8 * Math.cos(angle - Math.PI / 6), y2 - 8 * Math.sin(angle - Math.PI / 6)); ctx.lineTo(x2, y2); ctx.lineTo(x2 - 8 * Math.cos(angle + Math.PI / 6), y2 - 8 * Math.sin(angle + Math.PI / 6)); ctx.stroke()
    }
    if (shouldLabelExplanationEdge(bundle, edgeIndex, process ? [...activeEdges] : null)) {
      ctx.font = `20px ${SANS}`
      const allLines = textLines(ctx, edge.label, 160)
      const labels = allLines.slice(0, 2)
      if (allLines.length > 2) labels[1] = `${labels[1].slice(0, -1)}…`
      const width = Math.max(...labels.map(label => ctx.measureText(label).width), 0) + 12
      const height = labels.length * 22 + 6
      const gap = positioned.length > 9 ? 2 : 8
      const above = { x: midX - width / 2, y: Math.min(from.y, to.y) - height - gap }
      const below = { x: midX - width / 2, y: Math.max(from.y + from.height, to.y + to.height) + gap }
      const middle = { x: midX - width / 2, y: midY - height / 2 }
      const candidates = from.y === to.y
        ? (from.y < 350 ? [above, below, middle] : [below, above, middle])
        : [middle, above, below]
      candidates.push({ x: middle.x - width - 12, y: middle.y }, { x: middle.x + width + 12, y: middle.y })
      const box = placeExplanationLabel(candidates, width, height, [...positioned, ...occupiedLabels])
      // Never force a caption into a node or another caption when space runs out.
      if (!box) return
      occupiedLabels.push(box)
      if (Math.abs(box.y + height / 2 - midY) > 25) {
        ctx.beginPath(); ctx.lineWidth = 1; ctx.strokeStyle = '#c8c1b7'; ctx.moveTo(midX, midY)
        ctx.lineTo(box.x + width / 2, box.y > midY ? box.y : box.y + height); ctx.stroke()
      }
      roundBox(ctx, box.x, box.y, width, height, 3, PALETTE.paper, null)
      ctx.fillStyle = active ? PALETTE.accent : PALETTE.muted; ctx.textAlign = 'center'
      labels.forEach((label, lineIndex) => ctx.fillText(label, box.x + width / 2, box.y + 21 + lineIndex * 22)); ctx.textAlign = 'left'
    }
  })
  positioned.forEach((node, nodeIndex) => {
    const active = activeNodes.has(node.id), selected = node.id === selectedId
    ctx.lineWidth = selected || active ? 2 : 1
    roundBox(ctx, node.x, node.y, node.width, node.height, 7, active ? PALETTE.soft : PALETTE.card, selected || active ? PALETTE.accent : PALETTE.line)
    paintText(ctx, String(nodeIndex + 1).padStart(2, '0'), node.x + 16, node.y + 20, node.width - 32, { size: 10, color: PALETTE.muted, limit: 1 })
    paintText(ctx, node.label, node.x + 16, node.y + 47, node.width - 32, { size: 22, family: SERIF, color: active ? PALETTE.accent : PALETTE.ink, lineHeight: 27, limit: 2 })
  })
  ctx.fillStyle = '#f2f0ec'; ctx.fillRect(0, 582, 1280, 138)
  ctx.font = `22px ${SANS}`
  const captionLines = textLines(ctx, process ? step.caption : bundle.summary, 1140)
  const pages = Math.max(1, Math.ceil(captionLines.length / 3))
  const page = process ? Math.min(pages - 1, Math.floor(progress * pages)) : 0
  ctx.fillStyle = PALETTE.ink
  captionLines.slice(page * 3, page * 3 + 3).forEach((line, lineIndex) => ctx.fillText(line, 70, 619 + lineIndex * 30))
  if (process) { ctx.fillStyle = PALETTE.accent; ctx.fillRect(0, 716, 1280 * clampStudio(time / bundle.duration, 0, 1), 4) }
  ctx.restore()
}

function StudioPanel({ resource, bundle, error }) {
  const [view, setView] = useState('text')
  const [selectedId, setSelectedId] = useState(bundle?.nodes[0]?.id || '')
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [recordStatus, setRecordStatus] = useState('idle')
  const [recordError, setRecordError] = useState('')
  const [clip, setClip] = useState(null)
  const canvasRef = useRef(null)
  const animationRef = useRef(null)
  const clockRef = useRef({ time: 0, previous: 0, lastUI: 0, playing: false })
  const drawRef = useRef(null)
  const tickRef = useRef(null)
  const recorderRef = useRef(null)
  const urlRef = useRef(null)
  const aliveRef = useRef(true)
  const positioned = useMemo(() => bundle ? layoutExplanation(bundle) : [], [bundle])
  const codec = useMemo(() => selectStudioCodec(typeof window === 'undefined' ? null : window.MediaRecorder), [])
  const recordingSupported = Boolean(codec && typeof HTMLCanvasElement !== 'undefined' && HTMLCanvasElement.prototype.captureStream)
  const busy = recordStatus !== 'idle'
  const located = locateExplanationStep(bundle, time)
  const selectedNode = bundle?.nodes.find(node => node.id === selectedId)
  const total = bundle?.duration || 0
  const pausePlayback = useCallback(() => {
    clockRef.current.playing = false
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current)
    animationRef.current = null
    if (aliveRef.current) { setPlaying(false); setTime(clockRef.current.time) }
  }, [])

  const stopRecording = useCallback((cancel = false) => {
    const running = recorderRef.current
    if (!running) return
    if (cancel) running.cancelled = true
    if (running.instance.state !== 'inactive') {
      if (aliveRef.current) setRecordStatus('finishing')
      try { running.instance.stop() }
      catch (cause) {
        running.stream.getTracks().forEach(track => track.stop()); recorderRef.current = null
        if (aliveRef.current) { setRecordStatus('idle'); setRecordError(`录制结束失败：${cause.message}`) }
      }
    }
  }, [])

  useEffect(() => {
    tickRef.current = now => {
      const clock = clockRef.current
      if (!clock.playing || !bundle) return
      clock.time = Math.min(total, clock.time + Math.max(0, now - clock.previous) / 1000)
      clock.previous = now
      drawRef.current?.()
      if (now - clock.lastUI >= 80 || clock.time >= total) { clock.lastUI = now; setTime(clock.time) }
      if (clock.time >= total) { pausePlayback(); stopRecording(false); return }
      animationRef.current = requestAnimationFrame(timestamp => tickRef.current?.(timestamp))
    }
  }, [bundle, total, pausePlayback, stopRecording])

  function startPlayback(fromStart = false) {
    if (!bundle || !total || view !== 'process') return
    const clock = clockRef.current
    if (fromStart || clock.time >= total) clock.time = 0
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current)
    clock.previous = performance.now(); clock.playing = true
    setPlaying(true); setTime(clock.time); drawRef.current?.()
    animationRef.current = requestAnimationFrame(timestamp => tickRef.current?.(timestamp))
  }

  function seek(nextTime) {
    if (busy) return
    pausePlayback(); clockRef.current.time = clampStudio(nextTime, 0, total)
    setTime(clockRef.current.time); drawRef.current?.()
  }

  function changeView(nextView) {
    pausePlayback()
    if (recorderRef.current) { stopRecording(true); setRecordError('录制已取消，可以回到过程演示重新导出。') }
    setView(nextView)
  }

  function beginRecording() {
    if (!recordingSupported || !total || view !== 'process' || busy || recorderRef.current) return
    setRecordError(''); pausePlayback(); clockRef.current.time = 0; setTime(0); drawRef.current?.()
    let stream
    try {
      stream = canvasRef.current.captureStream(30)
      const instance = new MediaRecorder(stream, { mimeType: codec.mimeType, videoBitsPerSecond: 4000000 })
      const running = { instance, stream, cancelled: false, chunks: [] }
      recorderRef.current = running
      instance.ondataavailable = event => { if (event.data.size > 0) running.chunks.push(event.data) }
      instance.onerror = event => {
        running.cancelled = true; pausePlayback()
        if (aliveRef.current) setRecordError(`视频编码失败：${event.error?.message || '浏览器编码器不可用'}。`)
        stopRecording(true)
      }
      instance.onstop = () => {
        stream.getTracks().forEach(track => track.stop())
        if (recorderRef.current === running) recorderRef.current = null
        if (!aliveRef.current) return
        setRecordStatus('idle')
        if (running.cancelled) return
        const mime = instance.mimeType || codec.mimeType
        const extension = mime.includes('mp4') ? 'mp4' : mime.includes('webm') ? 'webm' : ''
        const blob = new Blob(running.chunks, { type: mime })
        if (!blob.size || !extension) { setRecordError('浏览器没有输出可用视频文件，请重新录制或更换浏览器。'); return }
        if (urlRef.current) URL.revokeObjectURL(urlRef.current)
        const url = URL.createObjectURL(blob); urlRef.current = url
        const title = String(bundle.title || resource.title || 'NetMind讲解').replace(/[<>:"/\\|?*]/g, '').split('').filter(char => char.charCodeAt(0) >= 32).join('').slice(0, 100)
        setClip({ url, size: blob.size, extension, filename: `${title || 'NetMind讲解'}.${extension}` })
      }
      instance.start(200); setRecordStatus('recording'); startPlayback(true)
    } catch (cause) {
      if (recorderRef.current) { recorderRef.current.cancelled = true; if (recorderRef.current.instance.state !== 'inactive') { try { recorderRef.current.instance.stop() } catch { /* tracks are stopped below */ } } }
      stream?.getTracks().forEach(track => track.stop()); recorderRef.current = null
      setRecordStatus('idle'); setRecordError(`无法开始录制：${cause.message || '浏览器不支持此操作'}。`)
    }
  }

  useEffect(() => {
    drawRef.current = () => drawStudioFrame(canvasRef.current, bundle, positioned, clockRef.current.time, view, selectedId)
    if (view !== 'text' && bundle) drawRef.current()
  }, [view, bundle, selectedId, positioned])
  useEffect(() => {
    aliveRef.current = true
    const clock = clockRef.current
    const hidden = () => {
      if (!document.hidden) return
      clockRef.current.playing = false
      if (animationRef.current !== null) cancelAnimationFrame(animationRef.current)
      animationRef.current = null; setPlaying(false); setTime(clockRef.current.time)
      const running = recorderRef.current
      if (running && running.instance.state !== 'inactive') {
        running.cancelled = true; setRecordStatus('finishing'); running.instance.stop()
        setRecordError('录制已取消：页面进入后台。保持此页面可见后可以重新录制。')
      }
    }
    document.addEventListener('visibilitychange', hidden)
    return () => {
      aliveRef.current = false; clock.playing = false
      if (animationRef.current !== null) cancelAnimationFrame(animationRef.current)
      const running = recorderRef.current
      if (running) {
        running.cancelled = true
        if (running.instance.state !== 'inactive') { try { running.instance.stop() } catch { /* stream cleanup still runs */ } }
        running.stream.getTracks().forEach(track => track.stop())
      }
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      document.removeEventListener('visibilitychange', hidden)
    }
  }, [])

  const canvasClick = event => {
    if (busy) return
    const rectangle = canvasRef.current.getBoundingClientRect()
    const node = hitExplanationNode(positioned, (event.clientX - rectangle.left) / rectangle.width * 1280, (event.clientY - rectangle.top) / rectangle.height * 720)
    if (node) setSelectedId(node.id)
  }

  return <div className={styles.studio}>
    <div className={styles.viewBar} role="tablist" aria-label="讲解内容形式">{[['text', '讲解'], ['diagram', '图解'], ['process', '过程演示']].map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={view === key} disabled={key !== 'text' && (!bundle || (key === 'process' && !bundle.steps.length))} className={view === key ? styles.activeTab : ''} onClick={() => changeView(key)}>{label}</button>)}{bundle && <small>{bundle.nodes.length} 个概念 · {bundle.edges.length} 条关系</small>}</div>
    {error && <p className={styles.fallback} role="status">{error} 文字讲解仍可正常阅读。</p>}
    {view === 'text' ? <MarkdownContent content={resource.content} variant="document" /> : <div className={styles.visualView}>
      <div className={styles.sceneShell}><canvas ref={canvasRef} width="1280" height="720" onClick={canvasClick} aria-label={`${bundle.title || resource.title}，${view === 'process' ? '过程演示' : '概念关系图'}。可用下方节点按钮查看详情。`} />{view === 'process' && <span className={styles.sceneBadge}>{recordStatus === 'recording' ? '● 正在录制' : '字幕演示 · 无配音'}</span>}</div>
      {view === 'process' && <>
        <div className={styles.playControls}><button type="button" className={styles.playButton} disabled={busy} onClick={() => playing ? pausePlayback() : startPlayback()} aria-label={playing ? '暂停演示' : '播放演示'}><Icon type={playing ? 'pause' : 'play'} size={17} /></button><span>{studioTime(time)}</span><input type="range" aria-label="过程演示进度" min="0" max={total} step="0.1" value={time} disabled={busy} onChange={event => seek(Number(event.target.value))} /><span>{studioTime(total)}</span><button type="button" className={styles.iconButton} disabled={busy} onClick={() => seek(0)} aria-label="回到开头"><Icon type="reset" /></button></div>
        <div className={styles.caption}><span>第 {located.index + 1} / {bundle.steps.length} 步 · {located.step?.title}</span><p aria-live={playing ? 'off' : 'polite'}>{located.step?.caption}</p></div>
        <div className={styles.steps} aria-label="演示步骤">{bundle.steps.map((step, index) => <button key={index} type="button" disabled={busy} className={index === located.index ? styles.activeStep : ''} onClick={() => seek(step.start)}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{step.title}</strong><small>{studioTime(step.start)} — {studioTime(step.end)}</small></div></button>)}</div>
      </>}
      <div className={styles.nodeButtons} aria-label="查看图解节点">{bundle.nodes.map(node => <button key={node.id} type="button" disabled={busy} aria-pressed={node.id === selectedId} className={node.id === selectedId ? styles.selectedNode : ''} onClick={() => setSelectedId(node.id)}>{node.label}</button>)}</div>
      {selectedNode && <div className={styles.nodeDetail}><span>概念详情</span><strong>{selectedNode.label}</strong><p>{selectedNode.detail || '可以继续向 NetMind 追问这个概念。'}</p>{bundle.edges.some(edge => edge.from === selectedId || edge.to === selectedId) && <ul>{bundle.edges.filter(edge => edge.from === selectedId || edge.to === selectedId).map((edge, index) => <li key={index}>{bundle.nodes.find(node => node.id === edge.from)?.label} <span>— {edge.label || '关联'} →</span> {bundle.nodes.find(node => node.id === edge.to)?.label}</li>)}</ul>}</div>}
      {view === 'process' && <>
        <div className={styles.exportBar}><div><strong>导出这段讲解</strong><small>{recordingSupported ? `录制上方演示画布，输出 ${codec.extension.toUpperCase()} 字幕视频，无配音。` : '当前浏览器不支持画布视频录制，仍可正常播放演示。'}</small></div>{busy ? <button type="button" className={styles.actionButton} onClick={() => { pausePlayback(); stopRecording(true); setRecordError('录制已取消。') }} disabled={recordStatus === 'finishing'}><Icon type="close" />{recordStatus === 'finishing' ? '整理文件中…' : '取消录制'}</button> : <button type="button" className={`${styles.actionButton} ${styles.primaryButton}`} disabled={!recordingSupported || !total} onClick={beginRecording}><Icon type="film" />录制并导出</button>}</div>
        {recordStatus === 'recording' && <p className={styles.recordingStatus} role="status">正在实时录制 {studioTime(time)} / {studioTime(total)}，请保持页面可见。</p>}
        {recordError && <p className={styles.recordError} role="alert">{recordError}</p>}
        {clip && <div className={styles.clip}><div><Icon type="check" /><strong>视频已生成</strong><span>{(clip.size / 1024 / 1024).toFixed(1)} MB · {clip.extension.toUpperCase()}</span><a href={clip.url} download={clip.filename} className={styles.actionButton}><Icon type="download" />下载视频</a></div><video controls src={clip.url} preload="metadata" aria-label="已生成的字幕视频预览" /></div>}
      </>}
    </div>}
  </div>
}

export default function ExplanationStudio({ resource }) {
  const rawBundle = resource?.metadata?.explanation_bundle
  const parsed = useMemo(() => parseExplanationBundle(rawBundle), [rawBundle])
  const error = resource?.metadata?.visualization_error || parsed.error
  const bundle = error ? null : parsed.bundle
  if (!resource) return null
  if (!bundle && !error) return <MarkdownContent content={resource.content} variant="document" />
  const identity = `${resource.id || resource.title || 'lecture'}:${JSON.stringify(rawBundle)}`
  return <StudioPanel key={identity} resource={resource} bundle={bundle} error={error} />
}
