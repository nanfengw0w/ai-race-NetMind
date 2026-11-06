import test from 'node:test';
import assert from 'node:assert/strict';
import { parseExplanationBundle, locateExplanationStep, layoutExplanation, hitExplanationNode, selectStudioCodec, shouldLabelExplanationEdge, placeExplanationLabel } from './explanationStudioUtils.mjs';

function input() {
  return {
    schema_version: 1, title: '通用概念过程', summary: '资料中的关系',
    nodes: [{ id: 'origin', label: '起点', detail: '原始输入' }, { id: 'result', label: '结果', detail: '处理后的结果' }],
    edges: [{ from: 'origin', to: 'result', label: '转换' }],
    steps: [
      { title: '观察输入', caption: '先观察起点。', active_nodes: ['origin'], active_edges: [], duration: 2 },
      { title: '观察联系', caption: '沿转换关系观察结果。', active_nodes: ['result'], active_edges: [0], duration: 3 },
    ],
  };
}

test('every node and edge referenced by a scene must exist', () => {
  assert.ok(parseExplanationBundle(input()).bundle);
  const danglingNode = input(); danglingNode.edges[0].to = 'missing';
  assert.match(parseExplanationBundle(danglingNode).error, /不存在的节点/);
  const danglingStep = input(); danglingStep.steps[1].active_nodes.push('missing');
  assert.match(parseExplanationBundle(danglingStep).error, /第 2 步/);
  const danglingEdge = input(); danglingEdge.steps[0].active_edges = [1];
  assert.match(parseExplanationBundle(danglingEdge).error, /不存在的关系/);
  const fractionalIndex = input(); fractionalIndex.steps[0].active_edges = [0.5];
  assert.ok(parseExplanationBundle(fractionalIndex).error);
});

test('duplicate IDs and unbounded model output fail into readable fallback', () => {
  const duplicate = input(); duplicate.nodes[1].id = 'origin';
  assert.match(parseExplanationBundle(duplicate).error, /重复/);
  const oversized = input(); oversized.nodes = Array.from({ length: 13 }, (_, i) => ({ id: `n${i}`, label: `节点 ${i}` }));
  assert.ok(parseExplanationBundle(oversized).error);
  const badTime = input(); badTime.steps[0].duration = Number.NaN;
  assert.ok(parseExplanationBundle(badTime).error);
  assert.deepEqual(parseExplanationBundle(undefined), { bundle: null, error: '' });
});

test('seeking at the exact boundary enters the next step; final boundary retains final frame', () => {
  const { bundle } = parseExplanationBundle(input());
  assert.equal(locateExplanationStep(bundle, 0).index, 0);
  assert.equal(locateExplanationStep(bundle, 1.999).index, 0);
  assert.equal(locateExplanationStep(bundle, 2).index, 1);
  assert.equal(locateExplanationStep(bundle, 2).progress, 0);
  assert.equal(locateExplanationStep(bundle, 5).index, 1);
  assert.equal(locateExplanationStep(bundle, 5).progress, 1);
  assert.equal(locateExplanationStep(bundle, -10).time, 0);
  assert.equal(locateExplanationStep(bundle, 1000).time, 5);
});

test('cyclic graph layout keeps all nodes, and hit testing follows the actual node rectangle', () => {
  const raw = input(); raw.edges.push({ from: 'result', to: 'origin', label: '反馈' });
  const { bundle } = parseExplanationBundle(raw);
  const nodes = layoutExplanation(bundle);
  assert.equal(nodes.length, 2);
  assert.equal(hitExplanationNode(nodes, nodes[0].x + 2, nodes[0].y + 2).id, nodes[0].id);
  assert.equal(hitExplanationNode(nodes, 1, 1), null);
  assert.notEqual(nodes[0].x, nodes[1].x);
});

test('six-node process snakes across rows so its two cycle boundaries stay vertical', () => {
  const nodes = Array.from({ length: 6 }, (_, index) => ({ id: `n${index}`, label: `节点 ${index + 1}` }));
  const edges = nodes.map((node, index) => ({ from: node.id, to: nodes[(index + 1) % nodes.length].id, label: '继续' }));
  const layout = layoutExplanation({ nodes, edges });
  assert.ok(layout[0].x < layout[1].x && layout[1].x < layout[2].x);
  assert.ok(layout[3].x > layout[4].x && layout[4].x > layout[5].x);
  assert.equal(layout[2].x, layout[3].x, 'row transition must stay in the same column');
  assert.equal(layout[5].x, layout[0].x, 'cycle return must stay in the same column');
  assert.ok(layout[3].y - layout[2].y - layout[2].height >= 70, 'reserve room for readable two-line edge captions');
});

test('recording file extension matches the supported container', () => {
  assert.deepEqual(selectStudioCodec({ isTypeSupported: mime => mime === 'video/webm;codecs=vp8' }), { mimeType: 'video/webm;codecs=vp8', extension: 'webm' });
  assert.equal(selectStudioCodec({ isTypeSupported: () => false }), null);
});

test('dense graph overviews omit captions and process shows at most two short active labels', () => {
  const nodes = Array.from({ length: 10 }, (_, index) => ({ id: `n${index}`, label: `概念${index}` }));
  const edges = nodes.slice(1).map((node, index) => ({ from: nodes[index].id, to: node.id, label: '关系' }));
  const bundle = { nodes, edges };
  assert.equal(edges.filter((_, index) => shouldLabelExplanationEdge(bundle, index)).length, 0);
  assert.equal(edges.filter((_, index) => shouldLabelExplanationEdge(bundle, index, [1, 3, 5])).length, 2);
  edges[1].label = '这是一条应在节点详情完整阅读的很长关系说明';
  assert.equal(shouldLabelExplanationEdge(bundle, 1, [1]), false);
  assert.equal(edges[1].label, '这是一条应在节点详情完整阅读的很长关系说明');
});

test('edge labels skip occupied positions and disappear if every position collides', () => {
  const obstacle = { x: 50, y: 200, width: 200, height: 84 };
  const candidates = [{ x: 60, y: 210 }, { x: 300, y: 300 }];
  assert.deepEqual(placeExplanationLabel(candidates, 100, 28, [obstacle]), { x: 300, y: 300, width: 100, height: 28 });
  assert.equal(placeExplanationLabel(candidates.slice(0, 1), 100, 28, [obstacle]), null);
  assert.equal(placeExplanationLabel([{ x: 300, y: 300 }], 100, 28, [{ x: 290, y: 290, width: 200, height: 60 }]), null);
});
