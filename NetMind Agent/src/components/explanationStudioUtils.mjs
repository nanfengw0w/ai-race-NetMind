export const clampStudio = (value, min, max) => Math.min(max, Math.max(min, value));

const text = (value, max = 1600) => typeof value === 'string' ? value.trim().slice(0, max) : '';

/** Validates model data, including every relationship and active-step reference. */
export function parseExplanationBundle(raw) {
  if (raw === undefined || raw === null) return { bundle: null, error: '' };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) || raw.schema_version !== 1) return { bundle: null, error: '讲解图解的数据版本暂不支持。' };
  if (!Array.isArray(raw.nodes) || !raw.nodes.length || raw.nodes.length > 12) return { bundle: null, error: '图解需要 1—12 个有效节点。' };
  if (!Array.isArray(raw.edges) || raw.edges.length > 24) return { bundle: null, error: '图解关系数据不完整，或超过 24 条。' };
  if (!Array.isArray(raw.steps) || raw.steps.length > 8) return { bundle: null, error: '过程演示数据不完整，或超过 8 个步骤。' };
  const nodes = raw.nodes.map(node => ({ id: text(node?.id, 64), label: text(node?.label, 100), detail: text(node?.detail, 2400) }));
  const ids = new Set(nodes.map(node => node.id));
  if (nodes.some(node => !node.id || !node.label) || ids.size !== nodes.length) return { bundle: null, error: '图解节点缺少名称，或存在重复编号。' };
  const edges = raw.edges.map(edge => ({ from: text(edge?.from, 64), to: text(edge?.to, 64), label: text(edge?.label, 120) }));
  if (edges.some(edge => !ids.has(edge.from) || !ids.has(edge.to))) return { bundle: null, error: '图解中有关系指向了不存在的节点。' };
  let start = 0;
  const steps = [];
  for (let index = 0; index < raw.steps.length; index += 1) {
    const step = raw.steps[index];
    const duration = step?.duration;
    if (typeof duration !== 'number' || !Number.isFinite(duration) || duration < 2 || duration > 12) return { bundle: null, error: `第 ${index + 1} 步的时长需要在 2—12 秒之间。` };
    const activeNodes = step?.active_nodes ?? [];
    const activeEdges = step?.active_edges ?? [];
    if (!Array.isArray(activeNodes) || activeNodes.some(id => typeof id !== 'string' || !ids.has(id))) return { bundle: null, error: `第 ${index + 1} 步引用了不存在的节点。` };
    if (!Array.isArray(activeEdges) || activeEdges.some(edgeIndex => !Number.isInteger(edgeIndex) || edgeIndex < 0 || edgeIndex >= edges.length)) return { bundle: null, error: `第 ${index + 1} 步引用了不存在的关系。` };
    steps.push({
      title: text(step.title, 120) || `步骤 ${index + 1}`,
      caption: text(step.caption, 2400), duration,
      active_nodes: [...new Set(activeNodes)], active_edges: [...new Set(activeEdges)],
      start, end: start + duration,
    });
    start += duration;
  }
  return { bundle: { schema_version: 1, title: text(raw.title, 160), summary: text(raw.summary, 2400), nodes, edges, steps, duration: start }, error: '' };
}

export function locateExplanationStep(bundle, rawTime) {
  const steps = bundle?.steps || [];
  if (!steps.length) return { index: -1, step: null, progress: 0, time: 0 };
  const duration = steps[steps.length - 1].end;
  const time = clampStudio(Number.isFinite(rawTime) ? rawTime : 0, 0, duration);
  const index = time >= duration ? steps.length - 1 : steps.findIndex(step => time < step.end);
  const step = steps[index];
  return { index, step, time, progress: clampStudio((time - step.start) / step.duration, 0, 1) };
}

/** Stable topological order; cyclic and disconnected nodes are retained. */
export function layoutExplanation(bundle) {
  const nodes = bundle?.nodes || [];
  const edges = bundle?.edges || [];
  const indegree = new Map(nodes.map(node => [node.id, 0]));
  edges.forEach(edge => { if (edge.from !== edge.to && indegree.has(edge.to)) indegree.set(edge.to, indegree.get(edge.to) + 1); });
  const queue = nodes.filter(node => indegree.get(node.id) === 0).map(node => node.id);
  const ordered = [];
  const used = new Set();
  while (queue.length) {
    const id = queue.shift();
    if (used.has(id)) continue;
    used.add(id); ordered.push(nodes.find(node => node.id === id));
    edges.filter(edge => edge.from === id && edge.from !== edge.to).forEach(edge => {
      if (!indegree.has(edge.to)) return;
      indegree.set(edge.to, indegree.get(edge.to) - 1);
      if (indegree.get(edge.to) === 0) queue.push(edge.to);
    });
  }
  nodes.forEach(node => { if (!used.has(node.id)) ordered.push(node); });
  const columns = Math.min(nodes.length > 9 ? 4 : 3, Math.max(1, nodes.length));
  const rows = Math.ceil(nodes.length / columns);
  const gapX = 44, gapY = rows >= 3 ? 54 : 82;
  const nodeWidth = Math.min(340, (1120 - gapX * (columns - 1)) / columns);
  const nodeHeight = 84;
  const startX = (1280 - (nodeWidth * columns + gapX * (columns - 1))) / 2;
  const startY = 195 + (366 - (rows * nodeHeight + gapY * (rows - 1))) / 2;
  return ordered.map((node, index) => {
    const row = Math.floor(index / columns);
    const column = row % 2 === 0 ? index % columns : columns - 1 - index % columns;
    return { ...node, x: startX + column * (nodeWidth + gapX), y: startY + row * (nodeHeight + gapY), width: nodeWidth, height: nodeHeight };
  });
}

export function hitExplanationNode(nodes, x, y) {
  return nodes.find(node => x >= node.x && x <= node.x + node.width && y >= node.y && y <= node.y + node.height) || null;
}

// Dense overviews show relationships as lines; full wording remains in the
// selected node's relation list. Process frames label only the current edges.
export function shouldLabelExplanationEdge(bundle, edgeIndex, activeEdges = null) {
  const label = bundle.edges[edgeIndex]?.label || '';
  if (!label || Array.from(label).length > 12) return false;
  if (activeEdges) return activeEdges.slice(0, 2).includes(edgeIndex);
  return bundle.nodes.length <= 6 && bundle.edges.length <= 6 && edgeIndex < 3;
}

export function placeExplanationLabel(candidates, width, height, obstacles) {
  for (const candidate of candidates) {
    const box = { x: clampStudio(candidate.x, 12, 1268 - width), y: clampStudio(candidate.y, 182, 575 - height), width, height };
    const collides = obstacles.some(obstacle => box.x < obstacle.x + obstacle.width + 5
      && box.x + width + 5 > obstacle.x && box.y < obstacle.y + obstacle.height + 5
      && box.y + height + 5 > obstacle.y);
    if (!collides) return box;
  }
  return null;
}

export function selectStudioCodec(recorderClass) {
  if (!recorderClass?.isTypeSupported) return null;
  for (const mime of ['video/mp4;codecs=avc1.42E01E', 'video/mp4', 'video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']) {
    if (recorderClass.isTypeSupported(mime)) return { mimeType: mime, extension: mime.includes('mp4') ? 'mp4' : 'webm' };
  }
  return null;
}

export function studioTime(rawTime) {
  const time = Math.max(0, Math.floor(Number.isFinite(rawTime) ? rawTime : 0));
  return `${Math.floor(time / 60)}:${String(time % 60).padStart(2, '0')}`;
}
