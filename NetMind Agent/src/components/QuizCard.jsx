import { useState } from 'react'
import { cx } from '../utils/classNames'
import { createRemoteNote, recordLearningEvent, runTutorFollowup, submitQuiz } from '../services/api'
import { addQuizNote } from '../utils/notes'
import styles from './QuizCard.module.scss'

const STORAGE_KEY = 'netmind_noted_quizzes'

const CHOICE_TYPES = ['choice', 'multiple-choice', 'single-choice', 'select', '单选题', '选择题', '单选']
const MULTI_TYPES = ['multi', 'multi-choice', 'multiple-select', '多选题', '多选']
const JUDGE_TYPES = ['judge', 'true-false', 'true_false', 'truefalse', 'boolean', 'tf', 'judgement', 'judgment', '判断题', '判断']
const SHORT_TYPES = ['short', 'short-answer', 'fill', 'fill-in-the-blank', '简答题', '简答', '填空题']

const TYPE_LABELS = {
  choice: '单选题',
  multi: '多选题',
  judge: '判断题',
  fill: '填空题',
  short: '简答题',
  order: '排序题',
  code: '代码题',
  'multiple-choice': '选择题',
  'fill-in-the-blank': '填空题',
  'short-answer': '简答题',
}

function isChoiceType(type) {
  return CHOICE_TYPES.includes(type) || MULTI_TYPES.includes(type)
}

function isJudgeType(type) {
  return JUDGE_TYPES.includes(type)
}

function optionToText(option) {
  if (typeof option === 'string') return option
  if (option && typeof option === 'object') {
    return option.text || option.content || option.value || JSON.stringify(option)
  }
  return String(option)
}

function normalizeJudgeAnswer(value) {
  const raw = optionToText(value).trim().toLowerCase()
  if (raw === 'a') return true
  if (raw === 'b') return false
  const text = raw.replace(/^[a-z](?:[.、)、\s]+)/i, '')
  if (['true', 't', 'yes', 'y', '1', 'correct', 'right', '对', '正确', '是', '真'].includes(text)) return true
  if (['false', 'f', 'no', 'n', '0', 'incorrect', 'wrong', '错', '错误', '否', '假'].includes(text)) return false
  return null
}

function isCorrectOption(option, optionIndex, answer, questionType = '') {
  if (!answer) return false

  const optText = optionToText(option).trim()
  const answerText = Array.isArray(answer) ? answer.join(',') : String(answer).trim()
  if (questionType === 'judge') {
    const expected = normalizeJudgeAnswer(answerText)
    const candidate = normalizeJudgeAnswer(optText)
    if (expected !== null && candidate !== null) return expected === candidate
  }
  const optionLetter = String.fromCharCode(65 + optionIndex)
  const optWithoutLetter = optText.replace(/^[A-Z][.、)、\s]*/i, '').trim()

  return (
    optText === answerText ||
    optWithoutLetter === answerText ||
    optText.endsWith(answerText) ||
    optText.startsWith(answerText) ||
    answerText.includes(optionLetter) ||
    answerText.includes(optWithoutLetter)
  )
}

function loadNotedSet() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw ? new Set(JSON.parse(raw)) : new Set()
  } catch {
    return new Set()
  }
}

function saveNotedSet(set) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]))
  } catch {
    // This optional marker must not prevent note-taking when browser storage is unavailable.
  }
}

function restoreFromRecord(record, options) {
  if (!record) return null
  const userAnswer = record.user_answer || ''
  let selected = null
  if (options && options.length > 0) {
    const idx = options.findIndex(opt => optionToText(opt) === userAnswer)
    if (idx >= 0) selected = idx
  }
  return {
    selected,
    showAnswer: true,
    textAnswer: userAnswer,
    submissionResult: {
      user_answer: userAnswer,
      correct_answer: '',
      is_correct: !!record.is_correct,
    },
  }
}

export default function QuizCard({ question, index, studentId, userId, onTutorResourcesGenerated, savedRecord }) {
  const { type, question: q, options, answer, explanation, quizId } = question
  const restored = restoreFromRecord(savedRecord, options)

  const [selected, setSelected] = useState(restored?.selected ?? null)
  const [showAnswer, setShowAnswer] = useState(restored?.showAnswer ?? false)
  const [textAnswer, setTextAnswer] = useState(restored?.textAnswer ?? '')
  const [submitting, setSubmitting] = useState(false)
  const [submissionResult, setSubmissionResult] = useState(restored?.submissionResult ?? null)
  const [tutorFeedback, setTutorFeedback] = useState(null)
  const [followupLoading, setFollowupLoading] = useState(false)
  const [followupDone, setFollowupDone] = useState(false)
  const [noted, setNoted] = useState(() => {
    const set = loadNotedSet()
    return quizId ? set.has(quizId) : false
  })

  const handleToggleNoted = () => {
    const set = loadNotedSet()
    if (quizId) set.add(quizId)
    saveNotedSet(set)
    setNoted(true)
  }

  const submitToTutor = async (userAnswer) => {
    if (!quizId || !studentId) return
    setSubmitting(true)
    try {
      const result = await submitQuiz(studentId, quizId, userAnswer)
      setSubmissionResult(result)
      setTutorFeedback(result.tutor_action)
    } catch {
      setTutorFeedback({ message: '答案已显示，但学习状态同步失败。稍后可以再试一次。' })
    } finally {
      setSubmitting(false)
    }
  }

  const handleChoiceClick = async (i) => {
    if (showAnswer || submitting) return
    setSelected(i)
    setShowAnswer(true)
    await submitToTutor(optionToText(options[i]))
  }

  const handleSubmitText = async () => {
    if (!textAnswer.trim() || submitting) return
    setShowAnswer(true)
    await submitToTutor(textAnswer.trim())
  }

  const handleAddNote = () => {
    const answerText = Array.isArray(answer) ? answer.join(', ') : (answer || '')
    const optionTexts = (options || []).map(opt => typeof opt === 'string' ? opt : (opt?.text || opt?.content || opt?.value || JSON.stringify(opt)))
    const note = addQuizNote(q, optionTexts, answerText, explanation)
    createRemoteNote(userId, { ...note, studentId }).catch(() => {})
    recordLearningEvent(userId, {
      student_id: studentId,
      event_type: 'note_added',
      resource_type: 'quiz',
      title: `Q${index + 1}`,
      topic: question.topic || q.slice(0, 60),
      metadata: {
        source: 'quiz_note',
        content: q.slice(0, 240),
        answer: answerText,
      },
    }).catch(() => {})
    handleToggleNoted()
  }

  const handleTutorFollowup = async () => {
    if (!quizId || followupLoading || followupDone) return
    setFollowupLoading(true)
    try {
      const result = await runTutorFollowup(studentId, quizId, userId)
      onTutorResourcesGenerated?.(result.resources || [])
      setFollowupDone(true)
    } catch {
      setTutorFeedback(current => ({
        ...current,
        message: '后续资源暂时生成失败，可以稍后再试一次。',
      }))
    } finally {
      setFollowupLoading(false)
    }
  }

  const hasOptions = options && options.length > 0
  const isChoice = isChoiceType(type) || isJudgeType(type) || (hasOptions && !SHORT_TYPES.includes(type))

  return (
    <div className={styles.quizCard}>
      <div className={styles.quizHeader}>
        <span className={styles.quizTypeBadge}>{TYPE_LABELS[type] || type}</span>
        <div className={styles.quizHeaderRight}>
          <span className={styles.quizNumber}>Q{index + 1}</span>
          <button
            className={styles.quizNoteBtn}
            onClick={handleAddNote}
            disabled={noted}
            title={noted ? '已加入笔记' : '加入笔记'}
          >
            {noted ? '已加入' : '加入笔记'}
          </button>
        </div>
      </div>
      <h4>{q}</h4>

      {isChoice && hasOptions && (
        <div className={styles.quizOptions}>
          {options.map((opt, i) => {
            const optText = optionToText(opt)
            const isCorrectAnswer = isCorrectOption(opt, i, answer, type)
            const isSelected = selected === i
            let optState = null
            if (isSelected && showAnswer) {
              optState = isCorrectAnswer ? styles.correct : styles.wrong
            } else if (showAnswer && isCorrectAnswer) {
              optState = styles.correct
            } else if (isSelected) {
              optState = styles.selected
            }
            return (
              <div key={i} className={cx(styles.quizOption, optState)} onClick={() => handleChoiceClick(i)}>
                {optText}
              </div>
            )
          })}
        </div>
      )}

      {!isChoice && !showAnswer && (
        <div className={styles.quizTextAnswer}>
          <textarea
            rows={2}
            placeholder="输入你的答案..."
            value={textAnswer}
            onChange={e => setTextAnswer(e.target.value)}
          />
          <div className={styles.quizBtnRow}>
            <button onClick={handleSubmitText} disabled={!textAnswer.trim() || submitting}>
              {submitting ? '提交中...' : '提交答案'}
            </button>
            <button className={styles.quizShowBtn} onClick={() => setShowAnswer(true)}>查看答案</button>
          </div>
        </div>
      )}

      {showAnswer && (
        <div className={styles.quizExplanation}>
          <div className={styles.answerLabel}>
            <strong>正确答案：</strong>
            <span className={styles.answerText}>{Array.isArray(answer) ? answer.join(', ') : answer}</span>
          </div>
          {selected !== null && isChoice && (
            <div className={styles.answerResult}>
              {(() => {
                const optText = options[selected]
                const isCorrect = isCorrectOption(optText, selected, answer, type)
                return isCorrect
                  ? <span className={styles.resultCorrect}>回答正确</span>
                  : <span className={styles.resultWrong}>回答错误</span>
              })()}
            </div>
          )}
          {submissionResult && !isChoice && (
            <div className={styles.answerResult}>
              {submissionResult.is_correct
                ? <span className={styles.resultCorrect}>回答正确</span>
                : <span className={styles.resultWrong}>答案还不够准确，可以结合解析再试一道变式题</span>}
            </div>
          )}
          {explanation && <div className={styles.answerExplain}>{explanation}</div>}
          {tutorFeedback?.message && (
            <div className={styles.tutorFeedback}>
              <strong>Tutor Agent</strong>
              <span>{tutorFeedback.message}</span>
              {tutorFeedback.executable && tutorFeedback.label && (
                <button
                  className={styles.tutorAction}
                  onClick={handleTutorFollowup}
                  disabled={followupLoading || followupDone}
                >
                  {followupDone
                    ? '已加入学习工作台'
                    : followupLoading
                      ? '正在生成...'
                      : tutorFeedback.label}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
