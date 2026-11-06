import { cx } from '../utils/classNames'
import styles from './LearningPath.module.scss'

export default function LearningPath({ data }) {
  if (!data) return null

  const path = typeof data.content === 'string' ? JSON.parse(data.content) : data.content
  if (!path || !path.phases) return null

  return (
    <div className={styles.learningPath}>
      <div className={styles.pathHeader}>
        <h3>学习路径</h3>
        {path.total_duration && (
          <span className={styles.pathDuration}>总时长：{path.total_duration}</span>
        )}
      </div>
      <div className={styles.pathTimeline}>
        {path.phases.map((phase, i) => (
          <div key={i} className={styles.pathPhase}>
            <div className={styles.phaseMarker}>
              <div className={styles.phaseDot}></div>
              {i < path.phases.length - 1 && <div className={styles.phaseLine}></div>}
            </div>
            <div className={styles.phaseContent}>
              <div className={styles.phaseHeader}>
                <span className={styles.phaseNumber}>阶段 {phase.phase || i + 1}</span>
                <span className={styles.phaseTitle}>{phase.title}</span>
                {phase.duration && <span className={styles.phaseDuration}>{phase.duration}</span>}
              </div>
              {phase.tasks && (
                <div className={styles.phaseTasks}>
                  {phase.tasks.map((task, j) => (
                    <div key={j} className={styles.phaseTask}>
                      <span className={cx(styles.taskStatus, styles[task.status || 'pending'])}></span>
                      <span className={styles.taskText}>{task.task}</span>
                      {task.resource_type && (
                        <span className={styles.taskType}>{task.resource_type}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
