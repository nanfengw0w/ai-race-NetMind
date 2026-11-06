import { useState, useMemo, useEffect } from 'react'
import QuizCard from './QuizCard'
import ResourceSidebar from './ResourceSidebar'
import LearningPath from './LearningPath'
import MarkdownContent from './MarkdownContent'
import HtmlDeckViewer from './HtmlDeckViewer'
import InteractiveMindmap from './InteractiveMindmap'
import ExplanationStudio from './ExplanationStudio'
import InteractiveHtmlViewer from './InteractiveHtmlViewer'
import ArtifactWorkspace from './ArtifactWorkspace'
import { FILTER_OPTIONS } from '../constants/resources'
import { getQuizRecords, recordLearningEvent } from '../services/api'
import { cx } from '../utils/classNames'
import { resourceLifecycleTitle } from '../utils/resourceGeneration.mjs'
import { resourceTitleTopic } from '../utils/resourceTitles.mjs'
import { getInteractiveTitle } from '../utils/interactiveHtml.mjs'
import { artifactVersion } from '../utils/artifacts.mjs'
import styles from './ResourcePanel.module.scss'

const TYPE_LABELS = FILTER_OPTIONS.reduce((labels, item) => {
  labels[item.key] = item.label
  return labels
}, {})

const TYPE_HINTS = {
  document: '从对话保存的文稿与作品',
  lecture: '概念解释、类比、关键步骤',
  interactive: '调整参数、操作探索、即时观察变化',
  quiz: '选择题、填空题、简答题',
  mindmap: '结构化知识关系',
  ppt: '课件预览、下载保存',
  lab: '实验流程和观察点',
  learning_path: '阶段任务和学习顺序',
  assessment: '薄弱点和改进建议',
}

const STREAMING_TEXT_TYPES = new Set(['lecture', 'lab', 'assessment'])

const EMPTY_GUIDES = [
  { title: '看懂关系', text: '讲解卡片中查看图解，点开节点了解具体含义。' },
  { title: '动手探索', text: '选择「互动教学」，生成能调参数、看变化的学习内容。' },
  { title: '留下作品', text: '讲解、图示和过程演示保存在当前对话，方便继续探索。' },
]

function parseJson(value) {
  if (!value) return null
  if (typeof value !== 'string') return value

  const text = value.trim()
  const candidates = [text]
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fenced?.[1]) candidates.push(fenced[1].trim())

  const arrayStart = text.indexOf('[')
  const arrayEnd = text.lastIndexOf(']')
  if (arrayStart >= 0 && arrayEnd > arrayStart) {
    candidates.push(text.slice(arrayStart, arrayEnd + 1))
  }

  const objectStart = text.indexOf('{')
  const objectEnd = text.lastIndexOf('}')
  if (objectStart >= 0 && objectEnd > objectStart) {
    candidates.push(text.slice(objectStart, objectEnd + 1))
  }

  for (const candidate of candidates) {
    try {
      return JSON.parse(candidate)
    } catch {
      // Try the next possible JSON fragment.
    }
  }

  return null
}

function getQuestionArray(value) {
  const data = parseJson(value)
  if (Array.isArray(data)) return data
  if (Array.isArray(data?.questions)) return data.questions
  if (Array.isArray(data?.items)) return data.items
  if (Array.isArray(data?.quizzes)) return data.quizzes
  return []
}

function normalizeQuestionType(type = '') {
  const normalized = String(type).toLowerCase().trim().replace(/\s+/g, '-').replace(/_/g, '-')
  if (['multiple-choice', 'single-choice', 'choice', 'select'].includes(normalized)) return 'choice'
  if (['multi-choice', 'multi-select', 'multiple-select', 'multi'].includes(normalized)) return 'multi'
  if (['fill-in-the-blank', 'fill-blank', 'blank', 'fill'].includes(normalized)) return 'fill'
  if (['short-answer', 'short_answer', 'short'].includes(normalized)) return 'short'
  if (['true-false', 'truefalse', 'judge', 'judgement', 'judgment', 'boolean', 'tf', '判断题', '判断'].includes(normalized)) return 'judge'
  return type || 'short'
}

function formatOption(option, index, shouldPrefix) {
  const label = String.fromCharCode(65 + index)
  if (typeof option === 'object' && option !== null) {
    const optionLabel = option.label || option.key || label
    const text = option.text || option.content || option.value || JSON.stringify(option)
    return `${optionLabel}. ${text}`
  }

  const text = String(option)
  if (!shouldPrefix || /^[A-Z][.、)、\s]/i.test(text)) return text
  return `${label}. ${text}`
}

function normalizeQuizQuestion(item, index) {
  if (typeof item === 'string') {
    return {
      type: 'short',
      question: item,
      options: [],
      answer: '',
      explanation: '',
    }
  }

  const type = normalizeQuestionType(item?.type || item?.question_type)
  const rawOptions = Array.isArray(item?.options)
    ? item.options
    : Array.isArray(item?.choices)
      ? item.choices
      : []
  const normalizedOptions = type === 'judge' && rawOptions.length === 0
    ? ['正确', '错误']
    : rawOptions
  const shouldPrefixOptions = normalizedOptions.length > 0 && !normalizedOptions.every(option => (
    typeof option === 'string' && /^[A-Z][.、)、\s]/i.test(option)
  ))

  return {
    type,
    question: item?.question || item?.prompt || item?.title || `第 ${index + 1} 题`,
    options: normalizedOptions.map((option, optionIndex) => formatOption(option, optionIndex, shouldPrefixOptions)),
    answer: item?.answer ?? item?.correct_answer ?? item?.solution ?? '',
    explanation: item?.explanation || item?.analysis || item?.reason || '',
    quizId: item?.quiz_id || item?.quizId || null,
    topic: item?.topic || item?.knowledge_point || item?.related_topic || '',
  }
}

function parseQuizQuestions(content, metadata) {
  const fromMetadata = getQuestionArray(metadata?.questions)
  const fromMetadataRoot = getQuestionArray(metadata)
  const fromContent = getQuestionArray(content)
  const questions = fromMetadata.length > 0
    ? fromMetadata
    : fromMetadataRoot.length > 0
      ? fromMetadataRoot
      : fromContent

  return questions
    .map(normalizeQuizQuestion)
    .filter(item => item.question)
}

function asList(items) {
  if (!Array.isArray(items) || items.length === 0) return '- 暂无'
  return items.map(item => `- ${typeof item === 'string' ? item : JSON.stringify(item)}`).join('\n')
}

const KNOWN_TOPIC_RULES = [
  [/tcp\s*拥塞控制|拥塞控制/i, 'TCP拥塞控制'],
  [/tcp\s*三次握手|三次握手/i, 'TCP三次握手'],
  [/tcp\s*四次挥手|四次挥手/i, 'TCP四次挥手'],
  [/arp\s*协议|地址解析协议|arp/i, 'ARP协议'],
  [/dns|域名系统|域名解析/i, 'DNS'],
  [/https/i, 'HTTPS'],
  [/http/i, 'HTTP'],
  [/ipv6/i, 'IPv6'],
  [/ipv4/i, 'IPv4'],
  [/dhcp/i, 'DHCP'],
  [/nat|网络地址转换/i, 'NAT'],
  [/mac\s*地址|物理地址/i, 'MAC地址'],
  [/ip\s*地址/i, 'IP地址'],
  [/ip\s*协议/i, 'IP协议'],
  [/子网划分|子网掩码/i, '子网划分'],
  [/路由协议|路由表|路由器/i, '路由协议'],
  [/wireshark|抓包/i, 'Wireshark'],
  [/osi\s*七层|osi模型|七层模型/i, 'OSI七层模型'],
]

function knownTopic(value = '') {
  const text = String(value || '')
  const labels = KNOWN_TOPIC_RULES
    .filter(([pattern]) => pattern.test(text))
    .map(([, label]) => label)
  if (
    labels.includes('IPv4') &&
    labels.includes('IPv6') &&
    (/(区别|差异|对比|比较|异同|不同|地址格式|过渡|和|与|\/)/i.test(text) ||
      /ipv4.{0,24}ipv6|ipv6.{0,24}ipv4/i.test(text))
  ) {
    return 'IPv4/IPv6对比'
  }
  const match = KNOWN_TOPIC_RULES.find(([pattern]) => pattern.test(text))
  return match?.[1] || ''
}

function stripGenerationWords(value = '') {
  let text = String(value || '')
    .split(/\n\s*(?:HTML|PPT)课件风格：/)[0]
    .replace(/\s+/g, ' ')
    .trim()

  const patterns = [
    /围绕\s*(.+?)(?:(?:，|,|\s)*(?:生成|制作|设计|做一份|写|出)|[。；;！!?？]|$)/,
    /(?:复习|学习|了解|讲讲|讲解|解释)\s*[「《“"']?(.{2,36}?)[」》”"']?\s*(?:这个|这一)?(?:知识点|概念|内容|主题)?(?:[，,。；;！!?？]|$)/,
    /(?:什么是|何为)\s*[「《“"']?(.{2,32}?)[」》”"']?(?:[，,。；;！!?？]|$)/,
  ]
  for (const pattern of patterns) {
    const match = text.match(pattern)
    if (match?.[1]) {
      text = match[1].trim()
      break
    }
  }

  return text
    .replace(/^(?:请|帮我|给我|告诉我|我想|我需要|需要|重新|继续|先|再|可以)?\s*(?:生成|制作|设计|做一份|写一份|出一套|讲解|解释|复习|学习|了解|围绕|关于|针对)?\s*/, '')
    .replace(/(?:这个|这一)?(?:知识点|概念|内容|主题|课程|章节|讲解|练习题|题目|实操案例|实验|HTML课件|PPT课件|课件).*$/, '')
    .replace(/^[\s「」《》“”"'[\]【】，,。；;！!?？]+|[\s「」《》“”"'[\]【】，,。；;！!?？]+$/g, '')
}

function normalizeDisplayTopic(value = '') {
  const raw = String(value || '').trim()
  if (!raw) return ''

  const quoted = raw.match(/[「《“"']([^」》”"']{2,40})[」》”"']/)
  if (quoted?.[1]) {
    const topic = knownTopic(quoted[1]) || stripGenerationWords(quoted[1])
    if (topic && topic.length <= 16) return topic
  }

  const known = knownTopic(raw)
  if (known) return known

  let cleaned = stripGenerationWords(raw)
  const cleanedKnown = knownTopic(cleaned)
  if (cleanedKnown) return cleanedKnown

  cleaned = cleaned
    .split(/[：:，,。；;！!?？\n]/)[0]
    .replace(/(功能|原理|概念|定义|特点|作用|意义|优势|缺点|应用场景)$/, '')
    .replace(/在.{1,10}中/g, '')
    .replace(/的.{1,8}$/, '')
    .trim()
  const paired = cleaned.match(/^(\S{2,10})[与和、/]/)
  if (paired?.[1]) cleaned = paired[1]
  if (cleaned.length > 1 && cleaned.length <= 16 && !/(生成|制作|讲解|练习|需要|帮我|给我)/.test(cleaned)) {
    return cleaned
  }
  return ''
}

function getResourceTopic(resource) {
  const displayValue = value => resource?.type === 'interactive' ? getInteractiveTitle(value, '') : value
  const storedTitle = resourceTitleTopic(displayValue(resource?.title))
  if (storedTitle) return storedTitle
  const topics = resource?.metadata?.related_topics
  if (Array.isArray(topics) && topics.length > 0) {
    const topic = normalizeDisplayTopic(displayValue(topics[0]))
    if (topic) return topic
  }
  const metadataTopic = normalizeDisplayTopic(displayValue(resource?.metadata?.topic))
  if (metadataTopic) return metadataTopic
  const titleTopic = normalizeDisplayTopic(String(displayValue(resource?.title) || '').replace(/^(?:HTML\s*课件|PPT\s*课件|互动网页|互动教学)\s*[-—:：]\s*/i, ''))
  if (titleTopic) return titleTopic
  return displayValue(resource?.metadata?.topic) || displayValue(resource?.title) || ''
}

function getResourceDisplayTitle(resource) {
  if (!resource) return '等待生成学习资源'
  if (resource.type === 'document') return resource.title || '未命名作品'
  const lifecycleTitle = resourceLifecycleTitle(resource)
  if (lifecycleTitle) return lifecycleTitle
  const label = TYPE_LABELS[resource.type] || resource.type || '学习资源'
  const topic = getResourceTopic(resource)
  if (topic) return `${label} - ${topic}`
  return resource.title || label
}

function getResourceDisplaySubtitle(resource) {
  if (resource?.status === 'error') return '生成未完成'
  const topic = getResourceTopic(resource)
  if (topic) return topic
  return TYPE_HINTS[resource?.type] || '学习资源'
}

function legacyJsonToMarkdown(type, data) {
  if (typeof data?.raw === 'string') return data.raw

  if (type === 'assessment') {
    const score = data.overall_score ?? data.score ?? '待评估'
    return `# 学习评估报告

## 总体判断
- 综合分数：${score}

## 掌握较好的部分
${asList(data.strengths)}

## 仍需加强的部分
${asList(data.weaknesses)}

## 建议行动
${asList(data.recommendations)}

## 路径调整
- 下一步重点：${data.adjusted_path?.next_focus || '结合薄弱点继续巩固'}
- 可以降低优先级：${Array.isArray(data.adjusted_path?.skip_topics) ? data.adjusted_path.skip_topics.join('、') : '暂无'}`
  }

  return `\`\`\`json
${JSON.stringify(data, null, 2)}
\`\`\``
}

function ResourceContent({ resource, studentId, userId, onTutorResourcesGenerated, quizRecordsMap, pptPreview }) {
  if (!resource) return null

  const { type, content, metadata } = resource

  if (resource.status === 'pending' || (resource.status === 'generating' && !content)) {
    const generation = metadata?.generation_progress
    const progress = Math.max(0, Math.min(100, Number(generation?.progress || 0)))
    return (
      <div className={styles.resourceStreamPlaceholder}>
        <div className={styles.streamGridMark}></div>
        <div>
          <span>{resource.status === 'generating' ? '正在生成' : '排队等待'}</span>
          <h3>{getResourceDisplayTitle(resource) || '资源生成中'}</h3>
          {generation ? (
            <div className={styles.generationProgress}>
              <div className={styles.generationProgressCopy}>
                <strong>{generation.stage}</strong>
                <b>{progress}%</b>
              </div>
              <div className={styles.generationProgressTrack} aria-label="资源生成进度">
                <i style={{ width: `${Math.max(2, progress)}%` }}></i>
              </div>
              <p>{generation.message || 'AI 正在构建动画工程。'}</p>
            </div>
          ) : (
            <p>这个资源完成后会自动替换到这里，你可以先查看已经生成好的内容。</p>
          )}
        </div>
      </div>
    )
  }

  if (resource.status === 'generating' && content && STREAMING_TEXT_TYPES.has(type)) {
    return (
      <div className={styles.streamingDocument}>
        <MarkdownContent content={content} variant={type === 'lecture' ? 'document' : 'default'} />
        <span className={styles.streamCursor} aria-hidden="true"></span>
      </div>
    )
  }

  if (resource.status === 'error') {
    return (
      <div className={cx(styles.resourceStreamPlaceholder, styles.error)}>
        <div className={styles.streamGridMark}></div>
        <div>
          <span>生成失败</span>
          <h3>{getResourceDisplayTitle(resource) || '资源暂未生成'}</h3>
          <p>{resource.errorMessage || resource.content || '可以稍后重新生成这个资源。'}</p>
          {resource.errorDetail && <details><summary>查看检查详情</summary><p>{resource.errorDetail}</p></details>}
        </div>
      </div>
    )
  }

  if (type === 'lecture' && (metadata?.explanation_bundle || metadata?.visualization_error)) {
    return <ExplanationStudio key={resource.id || content} resource={resource} />
  }

  if (type === 'interactive') {
    return <InteractiveHtmlViewer key={resource.id || content} resource={resource} />
  }

  if (type === 'mindmap') {
    return <InteractiveMindmap key={content} content={content} />
  }

  if (type === 'quiz') {
    const parsedQuestions = parseQuizQuestions(content, metadata)
    if (parsedQuestions.length > 0) {
      return (
        <div className={styles.quizList}>
          {parsedQuestions.map((q, i) => (
            <QuizCard
              key={`${q.quizId || q.question}-${i}`}
              question={q}
              index={i}
              studentId={studentId}
              userId={userId}
              onTutorResourcesGenerated={onTutorResourcesGenerated}
              savedRecord={q.quizId ? quizRecordsMap?.[q.quizId] : null}
            />
          ))}
        </div>
      )
    }
  }

  if (type === 'learning_path') {
    return <LearningPath data={resource} />
  }

  if (type === 'ppt') {
    return (
      <HtmlDeckViewer
        key={resource.id || resource.title}
        resource={resource}
        studentId={studentId}
        userId={userId}
        page={pptPreview?.page}
        syncAttempt={pptPreview?.syncAttempt}
        onPageChange={pptPreview?.onPageChange}
        onPageUnavailable={pptPreview?.onPageUnavailable}
      />
    )
  }


  if (type === 'assessment') {
    const data = parseJson(content)
    if (data) {
      return <MarkdownContent content={legacyJsonToMarkdown(type, data)} />
    }
  }

  return <MarkdownContent content={content} variant={['lecture', 'document'].includes(type) ? 'document' : 'default'} />
}

function ResourceReferences({ metadata }) {
  const references = Array.isArray(metadata?.references) ? metadata.references : []
  if (references.length === 0) return null

  return (
    <aside className={styles.resourceReferences}>
      <div className={styles.referenceHeading}>
        <div>
          <span>RAG REFERENCES</span>
          <h3>参考资料</h3>
        </div>
        <small>本资源生成时检索了 {metadata?.retrieval?.matched_chunks || references.length} 个相关片段</small>
      </div>
      <div className={styles.referenceList}>
        {references.map((reference, index) => (
          <div
            className={styles.referenceItem}
            key={`${reference.source_file}-${reference.chapter}-${index}`}
          >
            <strong>{reference.source_file || '未命名资料'}</strong>
            <span>{reference.chapter || '未标注章节'}</span>
            {Number.isFinite(reference.relevance) && <small>相关度 {reference.relevance}%</small>}
          </div>
        ))}
      </div>
    </aside>
  )
}

function EmptyWorkspace({ loading }) {
  if (loading) {
    return (
      <div className={cx(styles.resourceEmpty, styles.resourceEmptyLoading)}>
        <div className={styles.loadingDots}><span></span><span></span><span></span></div>
        <div className={styles.emptyText}>正在搭建你的学习工作台</div>
      </div>
    )
  }

  return (
    <div className={styles.resourceEmpty}>
      <div className={styles.emptyIcon}>NM</div>
      <div className={styles.emptyText}>从一个具体学习需求开始</div>
      <div className={styles.emptyHint}>在左侧提问，或切到「生成资源」制作讲解、图示和过程演示。</div>
      <div className={styles.emptyGuideGrid}>
        {EMPTY_GUIDES.map(item => (
          <div className={styles.emptyGuide} key={item.title}>
            <strong>{item.title}</strong>
            <span>{item.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ResourcePanel({
  resources,
  loading,
  activeResource,
  setActiveResource,
  studentId,
  userId,
  onTutorResourcesGenerated,
  onReferenceChange,
  resourceReference,
  onArtifactSaved,
}) {
  const [filter, setFilter] = useState('all')
  const [quizRecordsMap, setQuizRecordsMap] = useState({})

  useEffect(() => {
    let active = true
    getQuizRecords(studentId)
      .then(records => {
        if (!active) return
        const map = {}
        for (const r of records) {
          if (!map[r.quiz_id]) {
            map[r.quiz_id] = r
          }
        }
        setQuizRecordsMap(map)
      })
      .catch(() => {})
    return () => { active = false }
  }, [studentId])

  const filteredResources = useMemo(() => {
    if (filter === 'all') return resources
    return resources.filter(r => r.type === filter)
  }, [resources, filter])

  const safeIndex = Math.min(activeResource, Math.max(filteredResources.length - 1, 0))
  const currentResource = filteredResources[safeIndex]
  const currentType = currentResource?.type

  useEffect(() => {
    if (!currentResource) onReferenceChange(studentId, null)
  }, [currentResource, onReferenceChange, studentId])

  useEffect(() => {
    if (!Number.isInteger(currentResource?.id) || !studentId || currentResource.status === 'pending') return
    recordLearningEvent(userId, {
      student_id: studentId,
      event_type: 'resource_view',
      resource_id: currentResource.id,
      resource_type: currentResource.type,
      title: currentResource.title || '',
      topic: getResourceTopic(currentResource),
      metadata: { source: 'resource_panel' },
    }).catch(() => {})
  }, [currentResource, studentId, userId])

  useEffect(() => {
    if (!Number.isInteger(currentResource?.id) || !studentId || currentResource.status === 'pending') return
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      recordLearningEvent(userId, {
        student_id: studentId,
        event_type: 'resource_active',
        resource_id: currentResource.id,
        resource_type: currentResource.type,
        title: currentResource.title || '',
        topic: getResourceTopic(currentResource),
        value: 30,
        metadata: { source: 'resource_panel' },
      }).catch(() => {})
    }, 30000)
    return () => window.clearInterval(timer)
  }, [currentResource, studentId, userId])

  return (
    <div className={styles.resourcePanel}>
      <div className={styles.resourceLayout}>
        {resources.length > 0 && (
          <ResourceSidebar
            filter={filter}
            setFilter={(f) => { setFilter(f); setActiveResource(0) }}
            resources={resources}
          />
        )}

        <div className={styles.resourceMain}>
          <div data-tour="resource-overview" className={styles.resourceTopbar}>
            <div>
              <span className={styles.eyebrow}>学习工作台</span>
              <h2 title={getResourceDisplayTitle(currentResource)}>
                {getResourceDisplayTitle(currentResource)}
              </h2>
            </div>
            {resources.length > 0 && (
              <div className={styles.resourceStats}>
                <span>{resources.length} 个资源</span>
                {currentType && <strong>{TYPE_LABELS[currentType] || currentType}</strong>}
              </div>
            )}
          </div>

          <div className={styles.resourceContent}>
            {filteredResources.length > 0 && (
              <div className={styles.resourceTabs} role="tablist" aria-label="资源列表">
                {filteredResources.map((r, i) => (
                  <button
                    key={`${r.type}-${i}`}
                    className={cx(styles.resourceTab, i === safeIndex && styles.active)}
                    onClick={() => setActiveResource(i)}
                    aria-pressed={i === safeIndex}
                    title={getResourceDisplayTitle(r)}
                  >
                    <span>{TYPE_LABELS[r.type] || r.type}</span>
                    <small>
                      {r.status === 'generating'
                          ? '正在生成中'
                          : r.status === 'pending'
                            ? '等待生成'
                            : getResourceDisplaySubtitle(r)}
                    </small>
                  </button>
                ))}
              </div>
            )}

            {resources.length === 0 && <EmptyWorkspace loading={loading} />}

            {resources.length > 0 && filteredResources.length === 0 && (
              <div className={styles.resourceEmpty}>
                <div className={styles.emptyText}>这个分类下暂时没有资源</div>
                <div className={styles.emptyHint}>切回「全部」查看已经生成的内容。</div>
              </div>
            )}

            {currentResource && (
              <section className={styles.resourceDocument}>
                <ArtifactWorkspace
                  key={`${currentResource.id}-${currentResource.type === 'ppt' ? 'ppt' : artifactVersion(currentResource)}-${currentResource.status || 'ready'}`}
                  resource={currentResource} studentId={studentId} userId={userId}
                  onReferenceChange={onReferenceChange} onArtifactSaved={onArtifactSaved} resourceReference={resourceReference}
                >{(displayed, pptPreview) => <>
                <ResourceContent
                  key={`${displayed.id}-${displayed.type === 'ppt' ? 'ppt' : artifactVersion(displayed)}`}
                  resource={displayed}
                  studentId={studentId}
                  userId={userId}
                  onTutorResourcesGenerated={onTutorResourcesGenerated}
                  quizRecordsMap={quizRecordsMap}
                  pptPreview={pptPreview}
                />
                <ResourceReferences metadata={displayed.metadata} />
                </>}</ArtifactWorkspace>
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
