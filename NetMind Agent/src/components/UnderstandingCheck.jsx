import { useEffect, useRef, useState } from 'react'
import { understandingCheckRequest } from '../services/understandingCheckApi'
import styles from './UnderstandingCheck.module.scss'

const outcomeLabels = { demonstrated: '已体现', partial: '部分体现', not_yet: '尚未体现', uncertain: '暂不能判断', met: '已体现', missing: '尚未体现', contradicted: '与原文不符' }
const modeLabel = attempt => {
  if (attempt?.number > 1 || attempt?.mode === 'retry') return `第${attempt.number}次 · 重试${attempt.hint_requested ? ' · 看过提示' : ''}`
  const groupHint = attempt?.group_hint_exposed
  const feedback = attempt?.feedback_exposed || (attempt?.mode === 'feedback' && !groupHint)
  const exposure = groupHint && feedback ? '提示/反馈' : groupHint ? '提示' : feedback ? '反馈' : ''
  if (attempt?.hint_requested || attempt?.mode === 'hinted') return `看过提示${exposure ? ` · 已看本组${exposure}` : ''}`
  return exposure ? `看过本组${exposure}` : '独立作答'
}

function AttemptResult({ attempt }) {
  const result = attempt.result
  return <>
    {attempt.user_answer != null && <details><summary>我的原始作答</summary><p>{attempt.user_answer}</p></details>}
    {attempt.hint_requested && attempt.hint && attempt.status !== 'pending' && <details><summary>本次已查看的提示</summary><p>{attempt.hint}</p></details>}
    {result && <div>
      <p><strong>AI 评阅 · {outcomeLabels[result.overall] || result.overall}</strong></p>
      <small>仅依据这次作答与冻结的作品原文判断；不确定的部分需要进一步核对。</small>
      <p>{result.rationale}</p>
      {result.criteria?.length > 0 && <details><summary>查看 {result.criteria.length} 项判分与证据</summary>{result.criteria.map((criterion, index) => <div className={styles.criterion} key={criterion.id || index}>
        <strong>{outcomeLabels[criterion.status] || criterion.status} · {criterion.description}</strong>
        <p>{criterion.rationale}</p>
        <details><summary>查看原文与作答依据</summary>
          <small>作品原文</small><blockquote>{criterion.source_quote || '本项未提供可核对的原文证据。'}</blockquote>
          <small>作答原句</small><blockquote>{criterion.student_answer_quote || '作答中未找到直接证据。'}</blockquote>
        </details>
      </div>)}</details>}
      {result.expected_answer && <details><summary>参考回答</summary><p>{result.expected_answer}</p></details>}
    </div>}
  </>
}

function Question({ question, checkId, identity, onCheck, actionBusy, beginAction, endAction }) {
  const attempt = question.latest_attempt || question.attempts?.at(-1)
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const lock = useRef(false)
  const storageKey = `netmind-understanding:${identity.userId}:${identity.studentId}:${checkId}:${question.id}:${attempt?.id}`
  const [pending, setPending] = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || 'null') } catch { return null }
  })
  const attempts = question.attempts || []
  const viewed = attempts.find(item => item.id === selectedId) || attempt
  const active = viewed?.id === attempt?.id
  const waiting = attempt?.status === 'grading'
  const canAnswer = active && attempt?.status === 'pending'

  async function act(action) {
    if (lock.current || !beginAction()) return
    lock.current = true
    setBusy(true)
    setError('')
    let request = pending
    try {
      if (action === 'submit' && !request) {
        request = { request_id: crypto.randomUUID(), user_answer: answer }
        // Keep the exact payload when the server received a submission but its response was lost.
        localStorage.setItem(storageKey, JSON.stringify(request))
        setPending(request)
      }
      const check = await understandingCheckRequest(checkId, identity, action, {
        question_id: question.id, attempt_id: attempt.id,
        ...(action === 'submit' ? request : {}),
      })
      onCheck(check)
      if (action === 'submit') { localStorage.removeItem(storageKey); setPending(null) }
      if (action === 'retry') { setAnswer(''); setSelectedId('') }
    } catch (failure) {
      if (action === 'submit' && [400, 422].includes(failure.status)) {
        setAnswer(request?.user_answer || answer)
        localStorage.removeItem(storageKey)
        setPending(null)
      }
      setError(failure.message || '请求未完成，请重试。')
    }
    finally { lock.current = false; setBusy(false); endAction() }
  }

  return <section aria-label="理解检查题目">
    <p><strong>{question.prompt}</strong></p>
    {attempts.length > 1 && <label>查看作答记录<select aria-label="查看作答记录" value={viewed?.id || ''} onChange={event => setSelectedId(event.target.value)}>
      {attempts.map(item => <option key={item.id} value={item.id}>{modeLabel(item)} · {({ pending: '待作答', grading: '评阅中', graded: '已评阅', failed: '评阅失败' })[item.status] || item.status}</option>)}
    </select></label>}
    <span className={styles.badge}>{modeLabel(viewed)}</span>
    {active && attempt?.status === 'pending' && attempt?.hint_requested && attempt.hint && <p className={styles.hint}>{attempt.hint}</p>}
    {canAnswer && <>
      <label>写下你的理解<textarea aria-label="写下你的理解" maxLength={20000} value={pending?.user_answer ?? answer} disabled={busy || Boolean(pending)} onChange={event => setAnswer(event.target.value)} placeholder="用自己的话解释，并说明理由…" /></label>
      <div className={styles.actions}>
        <button type="button" className={styles.primary} disabled={actionBusy || (!pending && !answer.trim())} onClick={() => act('submit')}>{busy ? '正在处理…' : pending ? '重试提交原作答' : '提交作答'}</button>
        {!attempt.hint_requested && <button type="button" disabled={actionBusy || Boolean(pending)} onClick={() => act('hint')}>查看提示</button>}
      </div>
      <small>查看提示会保存记录；重试将保留本次作答和评阅。</small>
      <small>独立作答表示未用提示，且未看本组其他题的提示或反馈。</small>
    </>}
    {waiting && active && <p role="status">AI 正在评阅，作答已保存。可稍后重新载入查看。</p>}
    {viewed?.error && <p className={styles.error}>评阅未完成：{viewed.error}</p>}
    {viewed && <AttemptResult attempt={viewed} />}
    {active && (['graded', 'failed'].includes(attempt?.status) || attempt?.recovery_available) && <div className={styles.actions}><button type="button" disabled={actionBusy} onClick={() => act('retry')}>{busy ? '正在创建…' : attempt.recovery_available ? '评阅等待过久，重新作答' : '再答一次'}</button></div>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </section>
}

export default function UnderstandingCheck({ checkId, studentId, userId }) {
  const [check, setCheck] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionBusy, setActionBusy] = useState(false)
  const actionLock = useRef(false)
  const identity = { studentId, userId }
  function beginAction() {
    if (actionLock.current) return false
    actionLock.current = true
    setActionBusy(true)
    return true
  }
  function endAction() { actionLock.current = false; setActionBusy(false) }
  async function refresh() {
    if (!beginAction()) return
    setLoading(true); setError('')
    try { setCheck(await understandingCheckRequest(checkId, identity)) }
    catch (failure) { setError(failure.message) }
    finally { setLoading(false); endAction() }
  }
  useEffect(() => {
    let active = true
    understandingCheckRequest(checkId, { studentId, userId }).then(value => { if (active) setCheck(value) }).catch(failure => { if (active) setError(failure.message) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [checkId, studentId, userId])
  const source = check?.source
  return <div className={styles.card}>
    <div className={styles.heading}><h4>理解检查</h4><span className={styles.badge}>作答记录会保存</span></div>
    {loading && <p role="status">正在载入检查记录…</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {source && <>
      <small>{source.title || '当前作品'} · v{source.version}{source.slide_number ? ` · 第${source.slide_number}页` : ''}</small>
      <small>检查依据已保存，后续修改作品不会改变本次记录。</small>
      <details><summary>查看本次检查依据</summary>{source.text ? <blockquote>{source.text}</blockquote> : <small>检查依据正在载入，请刷新记录。</small>}</details>
    </>}
    {(check?.questions || []).map(question => <Question key={`${question.id}-${question.latest_attempt?.id || question.attempts?.at(-1)?.id}`} question={question} checkId={checkId} identity={identity} onCheck={setCheck} actionBusy={actionBusy} beginAction={beginAction} endAction={endAction} />)}
    <div className={styles.actions}><button type="button" disabled={loading || actionBusy} onClick={refresh}>{loading ? '正在载入…' : error ? '重试载入' : '刷新记录'}</button></div>
  </div>
}
