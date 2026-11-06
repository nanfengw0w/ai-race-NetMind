import { useEffect, useState } from 'react'
import {
  cleanupResourceStorage,
  clearKnowledgeBaseFiles,
  deleteKnowledgeBaseFile,
  getAppSettings,
  getKnowledgeBaseJob,
  getPermanentMemory,
  getResourceStorage,
  updateAppSettings,
  updatePermanentMemory,
  uploadKnowledgeBase,
} from '../services/api'
import styles from './SettingsPanel.module.scss'

const BUILD_STAGES = [
  { key: 'uploading', label: '保存文件' },
  { key: 'parsing', label: '解析正文' },
  { key: 'chunking', label: '内容切片' },
  { key: 'embedding', label: '向量入库' },
  { key: 'completed', label: '建库完成' },
]

const formatBytes = (value = 0) => {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export default function SettingsPanel({ userId = 'local_user', onClose }) {
  const [activeSection, setActiveSection] = useState('knowledge')
  const [settings, setSettings] = useState(null)
  const [storage, setStorage] = useState(null)
  const [memory, setMemory] = useState({
    enabled: false,
    content: '',
    max_chars: 4000,
  })
  const [cleanupDays, setCleanupDays] = useState(30)
  const [selectedResourceTypes, setSelectedResourceTypes] = useState([])
  const [files, setFiles] = useState([])
  const [uploadJob, setUploadJob] = useState(null)
  const [form, setForm] = useState({
    knowledge_base: 'blank',
    base_url: '',
    model: '',
    api_key: '',
  })
  const [status, setStatus] = useState('loading')

  useEffect(() => {
    let active = true
    Promise.all([
      getAppSettings(),
      getResourceStorage(),
      getPermanentMemory(userId),
    ])
      .then(([result, storageResult, memoryResult]) => {
        if (!active) return
        setSettings(result)
        setStorage(storageResult)
        setMemory(memoryResult)
        setForm({
          knowledge_base: result.knowledge_base || 'blank',
          base_url: result.ai?.base_url || '',
          model: result.ai?.model || '',
          api_key: '',
        })
        setStatus('')
      })
      .catch(() => {
        if (active) setStatus('设置加载失败，请确认后端服务正在运行。')
      })
    return () => { active = false }
  }, [userId])

  useEffect(() => {
    if (!uploadJob?.id || ['completed', 'failed'].includes(uploadJob.status)) return undefined
    let active = true
    let timer

    const poll = async () => {
      try {
        const nextJob = await getKnowledgeBaseJob(uploadJob.id)
        if (!active) return
        setUploadJob(nextJob)
        if (nextJob.status === 'completed') {
          setSettings(current => ({
            ...current,
            knowledge_base: 'custom',
            custom_knowledge_base: nextJob.result,
          }))
          setFiles([])
          setStatus('uploaded')
          return
        }
        if (nextJob.status === 'failed') {
          setStatus(nextJob.message || '资料处理失败。')
          return
        }
        timer = window.setTimeout(poll, 600)
      } catch {
        if (active) setStatus('无法读取建库进度，请检查后端连接。')
      }
    }

    timer = window.setTimeout(poll, 400)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [uploadJob?.id, uploadJob?.status])

  const save = async () => {
    setStatus('saving')
    try {
      const payload = {
        knowledge_base: form.knowledge_base,
        ai: {
          base_url: form.base_url,
          model: form.model,
        },
      }
      if (form.api_key.trim()) {
        payload.ai.api_key = form.api_key.trim()
      }
      const [next, savedMemory] = await Promise.all([
        updateAppSettings(payload),
        updatePermanentMemory(userId, {
          enabled: memory.enabled,
          content: memory.content,
        }),
      ])
      setSettings(next)
      setMemory(savedMemory)
      setForm(current => ({ ...current, api_key: '' }))
      setStatus('saved')
      window.setTimeout(() => setStatus(''), 1600)
    } catch {
      setStatus('保存失败，请检查后端连接。')
    }
  }

  const toggleResourceType = (resourceType) => {
    setSelectedResourceTypes(current => (
      current.includes(resourceType)
        ? current.filter(item => item !== resourceType)
        : [...current, resourceType]
    ))
  }

  const cleanupResources = async () => {
    const scope = selectedResourceTypes.length
      ? selectedResourceTypes
          .map(type => storage?.by_type?.find(item => item.type === type)?.label || type)
          .join('、')
      : '全部类型'
    if (!window.confirm(
      `确定清理 ${cleanupDays} 天前的${scope}资源吗？相关题目记录也会一并删除，此操作不可恢复。`,
    )) return
    setStatus('cleaning')
    try {
      const result = await cleanupResourceStorage(cleanupDays, selectedResourceTypes)
      setStorage(result.storage)
      setStatus(`已清理 ${result.deleted_count} 个资源，释放 ${formatBytes(result.freed_bytes)}`)
    } catch {
      setStatus('资源清理失败，请检查后端连接。')
    }
  }

  const optionDescription = (key) => {
    if (key === 'custom') return '上传自己的课程资料并建立本地向量库，后续生成会优先参考这些资料。'
    if (key === 'blank') return '不检索系统课程库，适合其它课程或自由主题。'
    return '使用系统内置计算机网络课件、实验和 Wireshark 资料。'
  }

  const uploadFiles = async () => {
    if (!files.length) {
      setStatus('请先选择 txt/md/docx/pptx 资料文件。')
      return
    }
    setStatus('uploading')
    setUploadJob(null)
    try {
      const result = await uploadKnowledgeBase(files)
      setSettings(result.settings)
      setForm(current => ({ ...current, knowledge_base: 'custom' }))
      setUploadJob(result.job)
      setStatus('')
    } catch {
      setStatus('资料上传或建库失败，请确认后端服务正在运行。')
    }
  }

  const deleteFile = async (filename) => {
    if (isProcessing || !window.confirm(`确定删除“${filename}”吗？删除后会重新构建知识库。`)) return
    setStatus('')
    try {
      const result = await deleteKnowledgeBaseFile(filename)
      setUploadJob(result.job)
    } catch {
      setStatus('删除资料失败，请稍后重试。')
    }
  }

  const clearFiles = async () => {
    if (
      isProcessing
      || !window.confirm(`确定清空全部 ${customStats.files || 0} 个资料文件吗？此操作不可恢复。`)
    ) return
    setStatus('')
    try {
      const result = await clearKnowledgeBaseFiles()
      setUploadJob(result.job)
    } catch {
      setStatus('清空知识库失败，请稍后重试。')
    }
  }

  const customStats = uploadJob?.result || settings?.custom_knowledge_base || {}
  const displayJob = uploadJob || (customStats.chunks > 0 ? {
    status: 'completed',
    stage: 'completed',
    progress: 100,
    message: `当前知识库已完成处理，共 ${customStats.files || 0} 个文件、${customStats.chunks || 0} 个知识片段`,
  } : null)
  const currentStageIndex = BUILD_STAGES.findIndex(stage => stage.key === displayJob?.stage)
  const isProcessing = uploadJob && !['completed', 'failed'].includes(uploadJob.status)
  const indexedFiles = customStats.file_details?.filter(file => file.chunks > 0).length || 0
  const emptyFiles = Math.max((customStats.file_details?.length || 0) - indexedFiles, 0)

  return (
    <div className={styles.overlay} role="presentation" onClick={onClose}>
      <section className={styles.panel} role="dialog" aria-modal="true" aria-label="系统设置" onClick={event => event.stopPropagation()}>
        <header className={styles.header}>
          <div>
            <span>NETMIND SETTINGS</span>
            <h2>系统设置</h2>
            <p>选择基础知识库，并配置兼容 OpenAI 接口的大模型服务。</p>
          </div>
          <button type="button" title="关闭设置" onClick={onClose}>×</button>
        </header>

        <nav className={styles.settingsTabs} aria-label="设置分类">
          {[
            { key: 'knowledge', label: '知识库' },
            { key: 'storage', label: '资源存储' },
            { key: 'memory', label: '永久记忆' },
            { key: 'ai', label: 'AI 接口' },
          ].map(item => (
            <button
              type="button"
              key={item.key}
              className={activeSection === item.key ? styles.settingsTabActive : ''}
              aria-pressed={activeSection === item.key}
              onClick={() => setActiveSection(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className={styles.body}>
          {activeSection === 'knowledge' && (
          <section className={styles.block}>
            <div className={styles.blockTitle}>
              <span>Knowledge Base</span>
              <h3>基础知识库</h3>
            </div>
            <div className={styles.kbOptions}>
              {(settings?.knowledge_base_options || [
                { key: 'computer_network', label: '计算机网络预设' },
                { key: 'custom', label: '上传资料建库' },
                { key: 'blank', label: '空白知识库' },
              ]).map(option => (
                <button
                  type="button"
                  key={option.key}
                  className={form.knowledge_base === option.key ? styles.active : ''}
                  onClick={() => setForm(current => ({ ...current, knowledge_base: option.key }))}
                >
                  <strong>{option.label}</strong>
                  <span>{optionDescription(option.key)}</span>
                </button>
              ))}
            </div>

            {form.knowledge_base === 'custom' && (
              <div className={styles.customKbPanel}>
                <div className={styles.uploadHeader}>
                  <div>
                    <strong>上传资料建库</strong>
                    <p>资料会依次经过正文提取、内容切片与向量化，完成后供 Agent 检索。</p>
                  </div>
                  <div className={styles.kbHeaderActions}>
                    <div className={styles.kbMetrics}>
                      <span><strong>{customStats.files || 0}</strong> 文件</span>
                      <span><strong>{customStats.chunks || 0}</strong> 片段</span>
                      <span><strong>{indexedFiles}</strong> 已入库</span>
                    </div>
                    {customStats.files > 0 && (
                      <button
                        type="button"
                        className={styles.deleteAllButton}
                        onClick={clearFiles}
                        disabled={isProcessing}
                      >
                        一键删除全部
                      </button>
                    )}
                  </div>
                </div>

                <div className={styles.uploadActions}>
                  <label className={styles.filePicker}>
                    <input
                      type="file"
                      multiple
                      accept=".txt,.md,.markdown,.docx,.pptx"
                      onChange={event => setFiles(Array.from(event.target.files || []))}
                    />
                    <span>{files.length ? `重新选择（${files.length}）` : '选择资料'}</span>
                  </label>
                  <button type="button" className={styles.uploadButton} onClick={uploadFiles} disabled={isProcessing}>
                    {isProcessing ? '处理中...' : '上传并建库'}
                  </button>
                </div>

                {files.length > 0 && !uploadJob && (
                  <div className={styles.selectedFiles}>
                    {files.map(file => (
                      <span key={`${file.name}-${file.size}`}>
                        {file.name}<small>{formatBytes(file.size)}</small>
                      </span>
                    ))}
                  </div>
                )}

                {displayJob && (
                  <div className={styles.processingPanel}>
                    <div className={styles.progressHeading}>
                      <strong>{displayJob.message}</strong>
                      <span>{displayJob.progress || 0}%</span>
                    </div>
                    <div className={styles.progressTrack}>
                      <span style={{ width: `${displayJob.progress || 0}%` }} />
                    </div>
                    <div className={styles.stageGrid}>
                      {BUILD_STAGES.map((stage, index) => {
                        const isDone = displayJob.status === 'completed' || index < currentStageIndex
                        const isCurrent = index === currentStageIndex && displayJob.status !== 'completed'
                        return (
                          <div
                            key={stage.key}
                            className={`${styles.stage} ${isDone ? styles.stageDone : ''} ${isCurrent ? styles.stageCurrent : ''}`}
                          >
                            <span>{isDone ? '✓' : String(index + 1).padStart(2, '0')}</span>
                            <small>{stage.label}</small>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {(customStats.file_details?.length > 0 || customStats.chunk_previews?.length > 0) && (
                  <div className={styles.kbResult}>
                    <div className={styles.resultColumn}>
                      <div className={styles.resultHeading}>
                        <span className={styles.resultLabel}>SOURCE FILES</span>
                        <small>{indexedFiles} 已入库{emptyFiles > 0 ? ` · ${emptyFiles} 未提取正文` : ''}</small>
                      </div>
                      <div className={styles.fileResults}>
                        {customStats.file_details?.map(file => (
                          <div key={file.name}>
                            <div className={styles.fileNameRow}>
                              <strong title={file.name}>{file.name}</strong>
                              <div className={styles.fileControls}>
                                <em className={file.chunks > 0 ? styles.indexed : styles.empty}>
                                  {file.chunks > 0 ? '已入库' : '无正文'}
                                </em>
                                <button
                                  type="button"
                                  className={styles.deleteFileButton}
                                  title={`删除 ${file.name}`}
                                  aria-label={`删除 ${file.name}`}
                                  onClick={() => deleteFile(file.name)}
                                  disabled={isProcessing}
                                >
                                  ×
                                </button>
                              </div>
                            </div>
                            <span>{file.type.toUpperCase()} · {formatBytes(file.size_bytes)} · {file.chunks} 个片段</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className={styles.resultColumn}>
                      <div className={styles.resultHeading}>
                        <span className={styles.resultLabel}>INDEX PREVIEW</span>
                        <small>展示实际进入向量库的内容</small>
                      </div>
                      <div className={styles.chunkResults}>
                        {customStats.chunk_previews?.map((chunk, index) => (
                          <article key={`${chunk.source_file}-${chunk.chapter}-${index}`}>
                            <span>{chunk.chapter || chunk.source_file}</span>
                            <p>{chunk.excerpt}</p>
                          </article>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>
          )}

          {activeSection === 'storage' && (
          <section className={styles.block}>
            <div className={styles.blockTitle}>
              <span>Resource Storage</span>
              <h3>资源存储</h3>
            </div>
            <p className={styles.sectionIntro}>
              统计生成资源写入数据库的内容。
            </p>
            <div className={styles.storageSummary}>
              <div>
                <span>资源总数</span>
                <strong>{storage?.count || 0}</strong>
              </div>
              <div>
                <span>资源总占用</span>
                <strong>{formatBytes(storage?.total_bytes || 0)}</strong>
              </div>
              <div>
                <span>数据库内容</span>
                <strong>{formatBytes(storage?.db_bytes || 0)}</strong>
              </div>
            </div>
            <div className={styles.resourceTypeGrid}>
              {(storage?.by_type || []).map(item => {
                const selected = selectedResourceTypes.includes(item.type)
                return (
                  <button
                    type="button"
                    key={item.type}
                    className={selected ? styles.resourceTypeSelected : ''}
                    onClick={() => toggleResourceType(item.type)}
                    aria-pressed={selected}
                  >
                    <span>{item.label}</span>
                    <strong>{formatBytes(item.total_bytes)}</strong>
                    <small>{item.count} 个</small>
                  </button>
                )
              })}
            </div>
            <div className={styles.cleanupRow}>
              <label>
                <span>清理时间范围</span>
                <select
                  value={cleanupDays}
                  onChange={event => setCleanupDays(Number(event.target.value))}
                >
                  <option value={7}>7 天前</option>
                  <option value={30}>30 天前</option>
                  <option value={90}>90 天前</option>
                  <option value={180}>180 天前</option>
                  <option value={365}>1 年前</option>
                </select>
              </label>
              <p>
                {selectedResourceTypes.length
                  ? `仅清理已选择的 ${selectedResourceTypes.length} 类资源`
                  : '未选择类型时清理全部资源类型'}
              </p>
              <button type="button" className={styles.cleanupButton} onClick={cleanupResources}>
                清理旧资源
              </button>
            </div>
          </section>
          )}

          {activeSection === 'memory' && (
          <section className={styles.block}>
            <div className={styles.blockTitle}>
              <span>Permanent Memory</span>
              <h3>永久记忆</h3>
            </div>
            <div className={styles.memoryHeading}>
              <div>
                <strong>让 Agent 跨对话记住稳定信息</strong>
                <p>适合保存长期学习目标、专业背景、讲解偏好和需要持续关注的薄弱点。</p>
              </div>
              <label className={styles.switch}>
                <input
                  type="checkbox"
                  checked={Boolean(memory.enabled)}
                  onChange={event => setMemory(current => ({
                    ...current,
                    enabled: event.target.checked,
                  }))}
                />
                <span>{memory.enabled ? '已启用' : '已关闭'}</span>
              </label>
            </div>
            <textarea
              className={styles.memoryInput}
              value={memory.content}
              maxLength={memory.max_chars || 4000}
              disabled={!memory.enabled}
              onChange={event => setMemory(current => ({
                ...current,
                content: event.target.value,
              }))}
              placeholder="例如：我是软件工程专业学生；偏好先看图解再看公式；当前目标是准备计算机网络期末考试。"
            />
            <div className={styles.memoryMeta}>
              <span>{memory.content.length} / {memory.max_chars || 4000}</span>
              <span>记忆会参与对话和 Tutor Agent 资源规划，不会替代知识库事实。</span>
            </div>
          </section>
          )}

          {activeSection === 'ai' && (
          <section className={styles.block}>
            <div className={styles.blockTitle}>
              <span>AI Provider</span>
              <h3>AI 接口</h3>
            </div>
            <label>
              <span>BASE_URL</span>
              <input
                value={form.base_url}
                onChange={event => setForm(current => ({ ...current, base_url: event.target.value }))}
                placeholder="https://api.openai.com/v1"
              />
            </label>
            <label>
              <span>模型名</span>
              <input
                value={form.model}
                onChange={event => setForm(current => ({ ...current, model: event.target.value }))}
                placeholder="gpt-4o"
              />
            </label>
            <label>
              <span>API Key</span>
              <input
                value={form.api_key}
                type="password"
                onChange={event => setForm(current => ({ ...current, api_key: event.target.value }))}
                placeholder={settings?.ai?.api_key_configured ? '已配置，留空则不修改' : '请输入你的 API Key'}
              />
            </label>
            <p className={styles.hint}>
              当前项目读取的是后端运行时设置。初始值来自 `backend/.env` 的
              `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，在这里保存后会立即用于下一次模型调用。
            </p>
          </section>
          )}
        </div>
        <footer className={styles.footer}>
          <span className={styles.status}>
            {status === 'loading' && '正在加载设置...'}
            {status === 'saving' && '正在保存...'}
            {status === 'saved' && '已保存'}
            {status === 'cleaning' && '正在清理旧资源...'}
            {status === 'uploading' && '正在上传资料...'}
            {status === 'uploaded' && '知识库已更新'}
            {status && !['loading', 'saving', 'saved', 'cleaning', 'uploading', 'uploaded'].includes(status) && status}
          </span>
          <button type="button" onClick={save} disabled={status === 'loading' || status === 'saving' || status === 'cleaning' || isProcessing}>
            保存设置
          </button>
        </footer>
      </section>
    </div>
  )
}
