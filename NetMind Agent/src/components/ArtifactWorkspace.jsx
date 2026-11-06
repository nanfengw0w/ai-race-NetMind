import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getArtifact, getArtifactVersions, getArtifactSlides, restoreArtifact, reviseArtifact } from '../services/api'
import { artifactDownload, artifactReference, artifactVersion, canReviseArtifact, markdownParagraphs, artifactSelection } from '../utils/artifacts.mjs'
import styles from './ArtifactWorkspace.module.scss'

export default function ArtifactWorkspace({ resource, studentId, userId, onReferenceChange, onArtifactSaved, resourceReference, children }) {
  const [preview, setPreview] = useState(null)
  const [versionSnapshot, setVersionSnapshot] = useState(null)
  const [instruction, setInstruction] = useState('')
  const [choosingParagraph, setChoosingParagraph] = useState(false)
  const [slideSnapshot, setSlideSnapshot] = useState(null)
  const [choosingPage, setChoosingPage] = useState(false)
  const [pptPage, setPptPage] = useState(1)
  const [observedPage, setObservedPage] = useState(null)
  const [pageBridgeError, setPageBridgeError] = useState(null)
  const [slideRetry, setSlideRetry] = useState(0)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(false)
  const displayed = preview?.baseVersion === artifactVersion(resource) ? preview.resource : resource
  const versions = versionSnapshot?.baseVersion === artifactVersion(resource) ? versionSnapshot.items : null
  const version = artifactVersion(displayed)
  const latestVersion = Math.max(artifactVersion(resource), Number(resource.current_version) || 1, Number(displayed?.current_version) || 1)
  const reference = artifactReference({ ...displayed, current_version: latestVersion })
  const paragraphOptions = useMemo(() => markdownParagraphs(displayed), [displayed])
  const selection = useMemo(() => artifactSelection(displayed, resourceReference), [displayed, resourceReference])
  const slideKey = `${studentId}:${userId}:${resource.id}:${version}:${artifactVersion(resource)}`
  const slideOptions = slideSnapshot?.key === slideKey ? slideSnapshot.items : null
  const referenceId = reference?.resource_id
  const referenceVersion = reference?.version
  const referenceTitle = reference?.title
  const referenceCurrentVersion = reference?.current_version
  const isPpt = displayed.type === 'ppt' && referenceId != null
  const pageReferencePending = isPpt && Boolean(resourceReference?.pending_page || resourceReference?.reference_error)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    if (isPpt) return
    onReferenceChange(studentId, referenceId == null ? null : {
      resource_id: referenceId, version: referenceVersion, title: referenceTitle, current_version: referenceCurrentVersion,
    })
  }, [studentId, referenceId, referenceVersion, referenceTitle, referenceCurrentVersion, onReferenceChange, isPpt])

  // Load exact ranges for each displayed version, independently of the picker.
  // Never reuse old source offsets when a new version replaces the iframe.
  useEffect(() => {
    if (!isPpt) return
    let cancelled = false
    getArtifactSlides(referenceId, studentId, userId, referenceVersion).then(items => {
      if (cancelled) return
      if (!Array.isArray(items) || !items.length || items.some((item, index) => item.number !== index + 1
        || item.number !== item.selection?.slide_number
        || !artifactSelection(displayed, { ...reference, selection: item.selection }))) {
        throw new Error('页面无法精确定位，暂不支持自动引用。')
      }
      setSlideSnapshot({ key: slideKey, items })
    }).catch(cause => {
      if (!cancelled) setSlideSnapshot({ key: slideKey, items: [], error: cause.message || '无法读取页面' })
    })
    return () => { cancelled = true }
    // The key contains artifact + displayed version; mutable reference selection
    // and callbacks must not restart a request on every turn of a page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPpt, referenceId, referenceVersion, studentId, userId, slideKey, slideRetry])

  const handlePreviewPage = useCallback(({ page, total }) => {
    if (!Number.isInteger(page) || page < 1 || !Number.isInteger(total) || total < page) return
    setPptPage(page)
    setPageBridgeError(null)
    setObservedPage(current => current?.key === slideKey && current.page === page && current.total === total
      ? current : { key: slideKey, page, total })
  }, [slideKey])

  const handlePageUnavailable = useCallback(() => {
    setObservedPage(null)
    setPageBridgeError({ key: slideKey, message: '尚未确认预览页码，可重新打开预览，或点击“引用整份课件”继续。' })
  }, [slideKey])

  const pageError = slideSnapshot?.key === slideKey && slideSnapshot.error
    || pageBridgeError?.key === slideKey && pageBridgeError.message
    || observedPage?.key === slideKey && slideSnapshot?.key === slideKey
      && observedPage.total !== slideSnapshot.items.length && '预览页数与保存原文不一致，请引用整份课件继续。'

  useEffect(() => {
    if (!isPpt) return
    const ready = !pageError && observedPage?.key === slideKey && slideSnapshot?.key === slideKey
      && observedPage.total === slideSnapshot.items.length
    const pageSelection = ready ? slideSnapshot.items[observedPage.page - 1]?.selection : null
    onReferenceChange(studentId, {
      resource_id: referenceId, version: referenceVersion, title: referenceTitle,
      current_version: referenceCurrentVersion, selection_source: 'preview',
      ...(pageSelection ? { selection: pageSelection } : {
        pending_page: observedPage?.key === slideKey ? observedPage.page : pptPage,
        ...(pageError ? { reference_error: pageError } : {}),
      }),
    })
  }, [isPpt, slideKey, observedPage, slideSnapshot, pptPage, studentId, referenceId,
    referenceVersion, referenceTitle, referenceCurrentVersion, onReferenceChange, pageError])

  const choosePptPage = item => {
    if (observedPage?.key === slideKey && observedPage.page === item.number) {
      onReferenceChange(studentId, { ...reference, selection: item.selection, selection_source: 'preview' })
    } else {
      setObservedPage(null)
      setPptPage(item.number)
    }
  }

  const run = async (label, operation) => {
    if (busy) return
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await operation()
    } catch (cause) {
      if (mounted.current) setError(`作品操作失败：${cause.message || '请稍后重试。'}`)
    } finally {
      if (mounted.current) setBusy('')
    }
  }

  const loadVersions = () => run('正在读取版本', async () => {
    const result = await getArtifactVersions(resource.id, studentId, userId)
    if (mounted.current) setVersionSnapshot({ items: result, baseVersion: artifactVersion(resource) })
  })

  const clearSelection = () => {
    if (!isPpt) setSlideSnapshot(null)
    onReferenceChange(studentId, reference)
  }

  const selectVersion = (selectedVersion) => {
    clearSelection()
    return run('正在读取作品', async () => {
      const result = await getArtifact(resource.id, studentId, userId, selectedVersion)
      if (mounted.current) setPreview({ resource: result, baseVersion: artifactVersion(resource) })
    })
  }

  const commit = async (operation) => {
    const result = await operation()
    clearSelection()
    // Parent callback owns the originating session even if this viewer was closed.
    onArtifactSaved(studentId, result)
    if (mounted.current) {
      setNotice(result.unchanged ? '该页已与所选历史版本一致，未新增版本' : '')
      setPreview({ resource: result, baseVersion: artifactVersion(resource) })
      setInstruction('')
      setEditing(false)
      setVersionSnapshot(null)
    }
  }

  const downloadOriginal = () => {
    const { filename, text, mime } = artifactDownload(displayed)
    const url = URL.createObjectURL(new Blob([text], { type: mime }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <>
    {reference && <section className={styles.tools} aria-label="作品版本管理">
      <div className={styles.header}>
        <div className={styles.identity}>
          <span className={styles.versionStamp} aria-label={`版本 ${version}`}>v{version}</span>
          <div className={styles.identityText}>
            <strong title={displayed.title || '未命名作品'}>{displayed.title || '未命名作品'}</strong>
            <span className={styles.versionStatus}>{version === latestVersion ? '当前版本' : `历史预览 · 最新 v${latestVersion}`}</span>
          </div>
        </div>
        <div className={styles.actions}>
          <button type="button" disabled={Boolean(busy)} aria-expanded={Boolean(versions)}
            onClick={() => versions ? setVersionSnapshot(null) : loadVersions()}>{versions ? '收起历史' : '历史版本'}<span aria-hidden="true">{versions ? '−' : '+'}</span></button>
          <button type="button" onClick={downloadOriginal}>下载原文<span aria-hidden="true">↗</span></button>
          {canReviseArtifact(displayed.type) && <button className={styles.primary} type="button" aria-expanded={editing}
            onClick={() => setEditing(value => !value)}>{editing ? '收起修改' : '修改作品'}<span aria-hidden="true">{editing ? '−' : '↗'}</span></button>}
        </div>
      </div>
      <div className={styles.scopeBar}>
        <div className={styles.scope}>
          <span className={styles.scopeLabel}>当前范围</span>
          <strong>{pageReferencePending ? '正在同步页面…' : selection ? selection.slide_number ? `第 ${String(selection.slide_number).padStart(2, '0')} 页` : '所选段落' : '整份作品'}</strong>
          {selection && <button className={styles.quiet} type="button" onClick={clearSelection}>{selection.slide_number ? '清除页面' : '清除段落'}</button>}
        </div>
        <div className={styles.scopeActions}>
          {paragraphOptions && <button type="button" aria-expanded={choosingParagraph}
            onClick={() => setChoosingParagraph(value => !value)}>选择段落</button>}
          {isPpt && <button type="button" disabled={Boolean(busy)} aria-expanded={choosingPage}
            onClick={() => setChoosingPage(value => !value)}>选择页面</button>}
          <button type="button" onClick={() => onReferenceChange(studentId, reference)}>{isPpt ? '引用整份课件' : '引用此版本'}</button>
        </div>
      </div>
      {version !== latestVersion && <div className={styles.restoreBar}>
          <span>以此版本恢复</span>
          <button className={styles.quiet} type="button" disabled={Boolean(busy)} onClick={() => selectVersion(latestVersion)}>查看最新版 ↗</button>
          <button className={styles.outline} type="button" disabled={Boolean(busy)} onClick={() => run('正在恢复版本', () => commit(
            () => restoreArtifact(resource.id, studentId, userId, version, latestVersion),
          ))}>{displayed.type === 'ppt' ? '恢复整份课件为新版本' : '恢复为新版本'}</button>
          {displayed.type === 'ppt' && <button className={styles.primary} type="button" disabled={Boolean(busy) || !selection?.slide_number}
            onClick={() => run('正在恢复所选页面', () => commit(
              () => restoreArtifact(resource.id, studentId, userId, version, latestVersion, selection.slide_number),
            ))}>{selection?.slide_number ? `仅恢复第 ${selection.slide_number} 页` : '仅恢复所选页'}</button>}
      </div>}
      {displayed.type === 'ppt' && version !== latestVersion && <p className={styles.limit}>
        {selection?.slide_number
          ? `将 v${version} 的第 ${selection.slide_number} 页恢复到最新 v${latestVersion}，其他页面保持当前版本，并保存为新版本。`
          : `当前恢复来源为 v${version}。预览页同步后，可点击“仅恢复所选页”；也可通过“选择页面”翻到要恢复的页。`}
      </p>}
      {paragraphOptions && choosingParagraph && <div className={styles.paragraphs} aria-label="选择原文段落">
        {paragraphOptions.error ? <p role="status">{paragraphOptions.error}</p> : paragraphOptions.paragraphs.length === 0
          ? <p>暂无可选择的正文段落。</p>
          : paragraphOptions.paragraphs.map((item, index) => <button type="button" key={item.start}
            aria-pressed={selection?.start === item.start}
            onClick={() => onReferenceChange(studentId, { ...reference, selection: item })}>
            <strong>引用第{index + 1}段</strong><small>{Array.from(item.text).slice(0, 120).join('')}{Array.from(item.text).length > 120 ? '…' : ''}</small>
          </button>)}
      </div>}
      {isPpt && pageError && <p className={styles.error} role="alert">
        无法同步页面引用：{pageError} <button type="button" onClick={() => {
          setObservedPage(null)
          setPageBridgeError(null)
          setSlideRetry(value => value + 1)
        }}>重试</button>
      </p>}
      {isPpt && choosingPage && <div className={styles.pages} aria-label="选择PPT页面">
        {!slideOptions ? <p role="status">正在读取页面…</p> : slideOptions.length === 0 ? <p>暂无可选择的页面。</p>
          : slideOptions.map(item => <button type="button" key={item.number}
            aria-pressed={selection?.slide_number === item.number}
            onClick={() => choosePptPage(item)}>
            <span className={styles.pageNumber} aria-label={`第${item.number}页`}>{String(item.number).padStart(2, '0')}</span>
            <span className={styles.pageTitle}>{item.title}</span>
            <span className={styles.pageMark} aria-hidden="true">{selection?.slide_number === item.number ? '●' : '↗'}</span>
          </button>)}
      </div>}
      {!canReviseArtifact(displayed.type) && <p className={styles.limit}>此类作品支持查看、下载和恢复，暂不支持改写。</p>}
      {versions && <div className={styles.versions} aria-label="历史版本列表">
        {versions.map(item => <button
          type="button" key={item.version} disabled={Boolean(busy)}
          aria-pressed={version === item.version} onClick={() => selectVersion(item.version)}
        >
          <span className={styles.historyVersion}>v{item.version}</span>
          <span className={styles.historyContent}><strong>{item.title || resource.title}</strong>
            <small>{item.change_request || '初始作品'}</small></span>
          <span className={styles.historyMeta}>{version === item.version ? '正在预览' : '查看版本'}
            {item.created_at && <time>{item.created_at}</time>}</span>
        </button>)}
        {versions.length === 0 && <span>暂无历史版本。</span>}
      </div>}
      {canReviseArtifact(displayed.type) && editing && <form className={styles.revise} onSubmit={event => {
        event.preventDefault()
        if (!instruction.trim() || pageReferencePending) return
        run('正在生成新版本，请稍候', () => commit(() => reviseArtifact(
          resource.id, studentId, userId, version, latestVersion, instruction.trim(), selection,
        )))
      }}>
        <div className={styles.reviseHeading}>
          <label htmlFor={`revise-${resource.id}`}>{selection ? selection.slide_number ? `仅修改第 ${selection.slide_number} 页` : '仅修改所选段落' : '修改整份作品'}</label>
          <span>基于 v{version} · 保存为新版本</span>
        </div>
        <textarea id={`revise-${resource.id}`} value={instruction} disabled={Boolean(busy)}
          onChange={event => setInstruction(event.target.value)} placeholder="输入修改要求，例如：保留结构，补充结尾……" rows={2} />
        {pageReferencePending && <p role="status">正在确认当前页面，引用就绪后可修改；如需修改整份，请先点击“引用整份课件”。</p>}
        <button className={styles.primary} type="submit" disabled={Boolean(busy) || !instruction.trim() || pageReferencePending}>生成新版本 <span aria-hidden="true">↗</span></button>
      </form>}
      {busy && <p role="status">{busy}…</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>}
    {children(displayed, isPpt ? { page: pptPage, syncAttempt: slideRetry, onPageChange: handlePreviewPage, onPageUnavailable: handlePageUnavailable } : undefined)}
  </>
}
