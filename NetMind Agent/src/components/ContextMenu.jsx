import { useEffect, useRef } from 'react'
import styles from './ContextMenu.module.scss'

export default function ContextMenu({ x, y, onAdd, onClose }) {
  const ref = useRef(null)

  useEffect(() => {
    const handleClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        onClose()
      }
    }
    const handleScroll = () => onClose()
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('scroll', handleScroll, true)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('scroll', handleScroll, true)
    }
  }, [onClose])

  return (
    <div
      ref={ref}
      className={styles.contextMenu}
      style={{ left: x, top: y }}
      role="menu"
    >
      <button role="menuitem" onClick={onAdd}>
        <span className={styles.icon}>+</span>
        加入笔记
      </button>
    </div>
  )
}
