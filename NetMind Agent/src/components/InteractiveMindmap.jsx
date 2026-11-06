import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import styles from './InteractiveMindmap.module.scss'

const WIDTH = 1120
const HEIGHT = 640
const NODE_WIDTH = 154
const NODE_HEIGHT = 46
const NODE_GAP = 18
const GRID_SIZE = 9000
const LAYOUT_STORAGE_PREFIX = 'netmind:mindmap-layout:v2:'
const TAU = Math.PI * 2

function getNodeWidth(label = '') {
  return Math.max(NODE_WIDTH, Math.min(260, 42 + String(label).length * 13))
}

function getNodeFontSize(label = '') {
  const length = String(label).length
  if (length > 18) return 10
  if (length > 13) return 11
  return 13
}

function getDisplayLabel(label = '') {
  const text = String(label || '')
  const maxChars = Math.max(7, Math.floor((getNodeWidth(text) - 30) / 13))
  return text.length > maxChars ? `${text.slice(0, maxChars - 1)}…` : text
}

function cleanLabel(value = '') {
  return String(value)
    .replace(/^root\s*/i, '')
    .replace(/^\(\((.*)\)\)$/u, '$1')
    .replace(/^\((.*)\)$/u, '$1')
    .replace(/^\[\[(.*)\]\]$/u, '$1')
    .replace(/^\[(.*)\]$/u, '$1')
    .replace(/^"(.*)"$/u, '$1')
    .trim()
}

function parseMindmap(content = '') {
  const source = String(content || '')
    .replace(/^```(?:mermaid)?\s*/i, '')
    .replace(/```$/i, '')
  const lines = source.split(/\r?\n/)
  const nodes = []
  const edges = []
  const stack = []

  for (const rawLine of lines) {
    if (!rawLine.trim() || rawLine.trim().toLowerCase() === 'mindmap') continue
    const indent = rawLine.match(/^\s*/)?.[0]?.replace(/\t/g, '  ').length || 0
    const label = cleanLabel(rawLine.trim())
    if (!label) continue

    while (stack.length && indent <= stack[stack.length - 1].indent) stack.pop()
    const parent = stack[stack.length - 1]
    const node = {
      id: `mindmap-${nodes.length}`,
      label,
      depth: parent ? parent.node.depth + 1 : 0,
      parentId: parent?.node.id || null,
    }
    nodes.push(node)
    if (parent) edges.push({ id: `${parent.node.id}-${node.id}`, source: parent.node.id, target: node.id })
    stack.push({ indent, node })
  }

  return { nodes, edges }
}

function relaxOverlaps(nodes, positions) {
  const next = Object.fromEntries(
    Object.entries(positions).map(([id, position]) => [id, { ...position }]),
  )

  for (let iteration = 0; iteration < 120; iteration += 1) {
    let moved = false
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const first = nodes[i]
        const second = nodes[j]
        const firstPosition = next[first.id]
        const secondPosition = next[second.id]
        if (!firstPosition || !secondPosition) continue

        const minX = (getNodeWidth(first.label) + getNodeWidth(second.label)) / 2 + NODE_GAP
        const minY = NODE_HEIGHT + NODE_GAP
        let dx = secondPosition.x - firstPosition.x
        let dy = secondPosition.y - firstPosition.y
        if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) {
          dx = ((j % 2) * 2 - 1) * 0.01
          dy = ((i % 2) * 2 - 1) * 0.01
        }

        const overlapX = minX - Math.abs(dx)
        const overlapY = minY - Math.abs(dy)
        if (overlapX <= 0 || overlapY <= 0) continue

        moved = true
        const firstLocked = first.depth === 0
        const secondLocked = second.depth === 0
        const firstShare = firstLocked ? 0 : secondLocked ? 1 : 0.5
        const secondShare = secondLocked ? 0 : firstLocked ? 1 : 0.5

        if (overlapX < overlapY) {
          const direction = dx >= 0 ? 1 : -1
          firstPosition.x -= direction * overlapX * firstShare
          secondPosition.x += direction * overlapX * secondShare
        } else {
          const direction = dy >= 0 ? 1 : -1
          firstPosition.y -= direction * overlapY * firstShare
          secondPosition.y += direction * overlapY * secondShare
        }
      }
    }
    if (!moved) break
  }

  return next
}

function createLayout(nodes, edges) {
  if (!nodes.length) return {}
  const children = new Map(nodes.map(node => [node.id, []]))
  edges.forEach(edge => children.get(edge.source)?.push(edge.target))
  const root = nodes[0]
  const positions = {}
  const leafCount = new Map()

  function countLeaves(id) {
    const childIds = children.get(id) || []
    if (!childIds.length) {
      leafCount.set(id, 1)
      return 1
    }
    const count = childIds.reduce((sum, childId) => sum + countLeaves(childId), 0)
    leafCount.set(id, count)
    return count
  }

  function placeInSector(id, depth, startAngle, endAngle) {
    const childIds = children.get(id) || []
    const angle = (startAngle + endAngle) / 2
    const radius = 188 + (depth - 1) * 178
    positions[id] = {
      x: WIDTH / 2 + Math.cos(angle) * radius,
      y: HEIGHT / 2 + Math.sin(angle) * radius,
    }
    if (!childIds.length) return

    const availableSpan = Math.max(0.42, endAngle - startAngle)
    const fanSpan = childIds.length === 1
      ? Math.min(availableSpan, 0.82)
      : Math.min(availableSpan, Math.max(1.08, Math.min(2.72, childIds.length * 0.46 + 0.52)))
    let cursor = angle - fanSpan / 2
    const totalWeight = childIds.reduce((sum, childId) => sum + (leafCount.get(childId) || 1), 0)
    childIds.forEach((childId) => {
      const extent = fanSpan * ((leafCount.get(childId) || 1) / totalWeight)
      placeInSector(childId, depth + 1, cursor, cursor + extent)
      cursor += extent
    })
  }

  countLeaves(root.id)
  const descendants = children.get(root.id) || []
  positions[root.id] = { x: WIDTH / 2, y: HEIGHT / 2 }

  if (descendants.length === 1) {
    placeInSector(descendants[0], 1, -Math.PI / 2 - 1.35, -Math.PI / 2 + 1.35)
  } else if (descendants.length > 1) {
    let cursor = -Math.PI / 2 - Math.PI
    const totalWeight = descendants.reduce((sum, childId) => sum + (leafCount.get(childId) || 1), 0)
    descendants.forEach((childId) => {
      const extent = TAU * ((leafCount.get(childId) || 1) / totalWeight)
      placeInSector(childId, 1, cursor, cursor + extent)
      cursor += extent
    })
  }

  nodes.forEach((node, index) => {
    if (!positions[node.id]) {
      positions[node.id] = { x: WIDTH / 2 + index * 18, y: HEIGHT / 2 + index * 18 }
    }
  })
  return relaxOverlaps(nodes, positions)
}

function getLayoutBounds(nodes, positions) {
  if (!nodes.length) {
    return { minX: 0, minY: 0, maxX: WIDTH, maxY: HEIGHT, width: WIDTH, height: HEIGHT }
  }
  const bounds = nodes.reduce((result, node) => {
    const position = positions[node.id] || { x: 0, y: 0 }
    const halfWidth = getNodeWidth(node.label) / 2
    return {
      minX: Math.min(result.minX, position.x - halfWidth),
      minY: Math.min(result.minY, position.y - NODE_HEIGHT / 2),
      maxX: Math.max(result.maxX, position.x + halfWidth),
      maxY: Math.max(result.maxY, position.y + NODE_HEIGHT / 2),
    }
  }, { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity })
  return {
    ...bounds,
    width: Math.max(1, bounds.maxX - bounds.minX),
    height: Math.max(1, bounds.maxY - bounds.minY),
  }
}

function getInitialTransform(nodes, positions) {
  const bounds = getLayoutBounds(nodes, positions)
  const padding = 86
  const scale = Math.min(
    1,
    (WIDTH - padding * 2) / bounds.width,
    (HEIGHT - padding * 2) / bounds.height,
  )
  return {
    x: WIDTH / 2 - ((bounds.minX + bounds.maxX) / 2) * scale,
    y: HEIGHT / 2 - ((bounds.minY + bounds.maxY) / 2) * scale,
    scale,
  }
}

function edgePath(source, target) {
  return `M ${source.x} ${source.y} L ${target.x} ${target.y}`
}

function getPathToRoot(nodeId, nodeMap) {
  const result = new Set()
  let current = nodeMap.get(nodeId)
  while (current) {
    result.add(current.id)
    current = current.parentId ? nodeMap.get(current.parentId) : null
  }
  return result
}

function escapeXml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function safeFilename(value = '') {
  return String(value || 'mindmap')
    .replace(/[\\/:*?"<>|]/g, '')
    .replace(/\s+/g, '-')
    .slice(0, 48) || 'mindmap'
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function hashContent(value = '') {
  let hash = 2166136261
  const text = String(value || '')
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

function getStorageKey(content = '') {
  return `${LAYOUT_STORAGE_PREFIX}${hashContent(content)}`
}

function hasValidPositions(nodes, positions) {
  if (!positions || typeof positions !== 'object') return false
  return nodes.every(node => (
    positions[node.id]
    && Number.isFinite(positions[node.id].x)
    && Number.isFinite(positions[node.id].y)
  ))
}

function loadSavedLayout(key, nodes) {
  if (typeof window === 'undefined') return null
  try {
    const saved = JSON.parse(window.localStorage.getItem(key) || 'null')
    if (!saved || !hasValidPositions(nodes, saved.positions)) return null
    const transform = saved.transform || {}
    return {
      positions: saved.positions,
      transform: {
        x: Number.isFinite(transform.x) ? transform.x : 0,
        y: Number.isFinite(transform.y) ? transform.y : 0,
        scale: Number.isFinite(transform.scale) ? transform.scale : 1,
      },
      selectedId: nodes.some(node => node.id === saved.selectedId) ? saved.selectedId : nodes[0]?.id || null,
    }
  } catch {
    return null
  }
}

function saveLayout(key, layout) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(key, JSON.stringify(layout))
  } catch {
    // Layout persistence is a convenience feature; storage quota errors should not break the mindmap.
  }
}

function MindmapView({ content }) {
  const graph = useMemo(() => parseMindmap(content), [content])
  const nodeMap = useMemo(() => new Map(graph.nodes.map(node => [node.id, node])), [graph.nodes])
  const initialPositions = useMemo(() => createLayout(graph.nodes, graph.edges), [graph])
  const initialTransform = useMemo(
    () => getInitialTransform(graph.nodes, initialPositions),
    [graph.nodes, initialPositions],
  )
  const layoutStorageKey = useMemo(() => getStorageKey(content), [content])
  const savedLayout = useMemo(
    () => loadSavedLayout(layoutStorageKey, graph.nodes),
    [layoutStorageKey, graph.nodes],
  )
  const [positions, setPositions] = useState(savedLayout?.positions || initialPositions)
  const [selectedId, setSelectedId] = useState(savedLayout?.selectedId || graph.nodes[0]?.id || null)
  const [transform, setTransform] = useState(savedLayout?.transform || initialTransform)
  const interactionRef = useRef(null)
  const canvasRef = useRef(null)

  const selected = nodeMap.get(selectedId) || graph.nodes[0]
  const selectedPath = useMemo(() => getPathToRoot(selectedId, nodeMap), [nodeMap, selectedId])
  const selectedChildren = useMemo(
    () => graph.nodes.filter(node => node.parentId === selectedId),
    [graph.nodes, selectedId],
  )

  const resetView = useCallback(() => {
    setPositions(initialPositions)
    setTransform(initialTransform)
  }, [initialPositions, initialTransform])

  useEffect(() => {
    if (!graph.nodes.length) return
    saveLayout(layoutStorageKey, { positions, transform, selectedId })
  }, [graph.nodes.length, layoutStorageKey, positions, selectedId, transform])

  const zoom = useCallback((factor) => {
    setTransform(current => ({ ...current, scale: Math.max(0.58, Math.min(1.9, current.scale * factor)) }))
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined

    const handleWheel = (event) => {
      event.preventDefault()
      zoom(event.deltaY > 0 ? 0.9 : 1.1)
    }

    canvas.addEventListener('wheel', handleWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', handleWheel)
  }, [zoom])

  const handlePointerMove = useCallback((event) => {
    const interaction = interactionRef.current
    if (!interaction) return

    if (interaction.type === 'node') {
      const dx = (event.clientX - interaction.startX) / transform.scale
      const dy = (event.clientY - interaction.startY) / transform.scale
      setPositions(current => ({
        ...current,
        [interaction.id]: {
          x: interaction.origin.x + dx,
          y: interaction.origin.y + dy,
        },
      }))
    } else {
      setTransform(current => ({
        ...current,
        x: interaction.origin.x + event.clientX - interaction.startX,
        y: interaction.origin.y + event.clientY - interaction.startY,
      }))
    }
  }, [transform.scale])

  const endInteraction = useCallback(() => {
    interactionRef.current = null
  }, [])

  const preventTextSelection = useCallback((event) => {
    event.preventDefault()
    window.getSelection?.()?.removeAllRanges()
  }, [])

  const beginNodeDrag = useCallback((event, nodeId) => {
    preventTextSelection(event)
    event.stopPropagation()
    event.currentTarget.setPointerCapture?.(event.pointerId)
    setSelectedId(nodeId)
    interactionRef.current = {
      type: 'node',
      id: nodeId,
      startX: event.clientX,
      startY: event.clientY,
      origin: positions[nodeId],
    }
  }, [positions, preventTextSelection])

  const beginPan = useCallback((event) => {
    preventTextSelection(event)
    event.currentTarget.setPointerCapture?.(event.pointerId)
    interactionRef.current = {
      type: 'pan',
      startX: event.clientX,
      startY: event.clientY,
      origin: transform,
    }
  }, [preventTextSelection, transform])

  const exportImage = useCallback(() => {
    const padding = 54
    const bounds = graph.nodes.reduce((result, node) => {
      const position = positions[node.id]
      const halfWidth = getNodeWidth(node.label) / 2
      return {
        minX: Math.min(result.minX, position.x - halfWidth),
        minY: Math.min(result.minY, position.y - NODE_HEIGHT / 2),
        maxX: Math.max(result.maxX, position.x + halfWidth),
        maxY: Math.max(result.maxY, position.y + NODE_HEIGHT / 2),
      }
    }, { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity })
    const width = Math.ceil(bounds.maxX - bounds.minX + padding * 2)
    const height = Math.ceil(bounds.maxY - bounds.minY + padding * 2)
    const offsetX = padding - bounds.minX
    const offsetY = padding - bounds.minY
    const edgeMarkup = graph.edges.map((edge) => {
      const source = positions[edge.source]
      const target = positions[edge.target]
      const active = selectedPath.has(edge.source) && selectedPath.has(edge.target)
      return `<path d="${edgePath(source, target)}" fill="none" stroke="${active ? '#b23a2f' : '#9ca8a4'}" stroke-width="${active ? 2.8 : 1.7}"${active ? ' stroke-dasharray="8 5"' : ''}/>`
    }).join('')
    const nodeMarkup = graph.nodes.map((node) => {
      const position = positions[node.id]
      const width = getNodeWidth(node.label)
      const active = node.id === selectedId
      const root = node.depth === 0
      const fill = active ? '#fff8f6' : root ? '#1d2927' : node.depth === 1 ? '#eef3f1' : '#ffffff'
      const stroke = active ? '#b23a2f' : root ? '#1d2927' : node.depth === 1 ? '#78928a' : '#8f9b97'
      const textFill = active ? '#922d24' : root ? '#ffffff' : '#38423f'
      return `<g transform="translate(${position.x} ${position.y})"><title>${escapeXml(node.label)}</title><rect x="${-width / 2}" y="${-NODE_HEIGHT / 2}" width="${width}" height="${NODE_HEIGHT}" rx="5" fill="${fill}" stroke="${stroke}" stroke-width="${active ? 2.4 : 1.2}"/><text text-anchor="middle" dominant-baseline="central" fill="${textFill}" font-family="Microsoft YaHei, PingFang SC, sans-serif" font-size="${getNodeFontSize(node.label)}" font-weight="${active || root ? 600 : 400}">${escapeXml(getDisplayLabel(node.label))}</text></g>`
    }).join('')
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs><pattern id="export-grid" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M 28 0 L 0 0 0 28" fill="none" stroke="#dce2df" stroke-width="0.7"/></pattern></defs><rect width="100%" height="100%" fill="#f9faf9"/><rect width="100%" height="100%" fill="url(#export-grid)"/><g transform="translate(${offsetX} ${offsetY})">${edgeMarkup}${nodeMarkup}</g></svg>`
    const image = new Image()
    const svgUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
    image.onload = () => {
      const exportCanvas = document.createElement('canvas')
      const ratio = 2
      exportCanvas.width = width * ratio
      exportCanvas.height = height * ratio
      const context = exportCanvas.getContext('2d')
      context.scale(ratio, ratio)
      context.drawImage(image, 0, 0, width, height)
      exportCanvas.toBlob((blob) => {
        if (blob) downloadBlob(blob, `${safeFilename(graph.nodes[0]?.label)}-思维导图.png`)
        URL.revokeObjectURL(svgUrl)
      }, 'image/png')
    }
    image.onerror = () => URL.revokeObjectURL(svgUrl)
    image.src = svgUrl
  }, [graph.edges, graph.nodes, positions, selectedId, selectedPath])

  if (!graph.nodes.length) {
    return <div className={styles.empty}>思维导图暂时没有可展示的节点。</div>
  }

  return (
    <div className={styles.mindmap}>
      <div className={styles.topbar}>
        <div>
          <span className={styles.eyebrow}>Interactive Knowledge Graph</span>
          <h3>{graph.nodes[0].label}</h3>
          <p>拖拽节点调整结构，点击节点查看它在知识网络中的位置。</p>
        </div>
        <div className={styles.stats}>
          <span><strong>{graph.nodes.length}</strong> 节点</span>
          <span><strong>{graph.edges.length}</strong> 关系</span>
          <span><strong>{Math.max(...graph.nodes.map(node => node.depth)) + 1}</strong> 层级</span>
        </div>
      </div>

      <div className={styles.workspace}>
        <div className={styles.canvasShell}>
          <div className={styles.controls}>
            <button type="button" title="放大" onClick={() => zoom(1.14)}>+</button>
            <button type="button" title="缩小" onClick={() => zoom(0.88)}>−</button>
            <button type="button" title="重置视图" onClick={resetView}>↺</button>
            <button type="button" title="导出为 PNG 图片" onClick={exportImage}>⇩</button>
          </div>
          <svg
            ref={canvasRef}
            className={styles.canvas}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            onPointerDown={beginPan}
            onPointerMove={handlePointerMove}
            onPointerUp={endInteraction}
            onPointerCancel={endInteraction}
            onSelectStart={preventTextSelection}
          >
            <defs>
              <pattern id="mindmap-grid" width="28" height="28" patternUnits="userSpaceOnUse">
                <path d="M 28 0 L 0 0 0 28" fill="none" stroke="currentColor" strokeWidth="0.7" />
              </pattern>
            </defs>
            <g transform={`translate(${transform.x} ${transform.y}) scale(${transform.scale})`}>
              <rect
                className={styles.grid}
                x={-GRID_SIZE / 2}
                y={-GRID_SIZE / 2}
                width={GRID_SIZE}
                height={GRID_SIZE}
                fill="url(#mindmap-grid)"
              />
              {graph.edges.map(edge => {
                const source = positions[edge.source]
                const target = positions[edge.target]
                if (!source || !target) return null
                const active = selectedPath.has(edge.source) && selectedPath.has(edge.target)
                return (
                  <path
                    key={edge.id}
                    className={`${styles.edge} ${active ? styles.activeEdge : ''}`}
                    d={edgePath(source, target)}
                  />
                )
              })}

              {graph.nodes.map(node => {
                const position = positions[node.id]
                const active = node.id === selectedId
                const inPath = selectedPath.has(node.id)
                const width = getNodeWidth(node.label)
                return (
                  <g
                    key={node.id}
                    className={`${styles.node} ${styles[`depth${Math.min(node.depth, 3)}`]} ${active ? styles.selected : ''} ${inPath ? styles.inPath : ''}`}
                    transform={`translate(${position.x} ${position.y})`}
                    onPointerDown={event => beginNodeDrag(event, node.id)}
                  >
                    <title>{node.label}</title>
                    <rect x={-width / 2} y={-NODE_HEIGHT / 2} width={width} height={NODE_HEIGHT} rx="5" />
                    <text
                      style={{ fontSize: getNodeFontSize(node.label) }}
                      textAnchor="middle"
                      dominantBaseline="central"
                    >
                      {getDisplayLabel(node.label)}
                    </text>
                    <circle className={styles.anchor} cx={width / 2 - 8} cy="0" r="3" />
                  </g>
                )
              })}
            </g>
          </svg>
        </div>

        <aside className={styles.inspector}>
          <span className={styles.eyebrow}>Selected Node</span>
          <h4>{selected?.label}</h4>
          <div className={styles.nodeMeta}>
            <span>层级 {selected?.depth + 1}</span>
            <span>{selectedChildren.length} 个分支</span>
          </div>
          <p>{selectedChildren.length > 0 ? '继续沿着分支展开，建立从概念到细节的理解路径。' : '这是当前路径的末端节点，适合结合讲解或练习继续验证。'}</p>
          <div className={styles.branchList}>
            {selectedChildren.map(child => (
              <button type="button" key={child.id} onClick={() => setSelectedId(child.id)}>
                <span></span>{child.label}
              </button>
            ))}
          </div>
        </aside>
      </div>
    </div>
  )
}

export default function InteractiveMindmap({ content }) {
  return <MindmapView key={content || ''} content={content} />
}
