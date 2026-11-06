export const TOUR_STORAGE_KEY = 'netmind_onboarding_v1';

export const TOUR_STEPS = [
  { id: 'chat', target: 'composer', mode: 'chat', title: '先从一个问题开始', description: '在这里输入你想弄懂的问题。普通提问会直接回答，你可以继续追问，不必先选学科。', task: '试着写下一个问题，或填入示例后自己点击「发送」。', example: '请用一个生活中的例子解释：概率和频率有什么区别？', event: 'chat-sent' },
  { id: 'resource-mode', target: 'resource-mode', title: '换一种方式理解', description: '需要图解、课件或可操作的演示时，切换到「生成资源」。对话和制作内容有各自的入口。', task: '点击高亮的「生成资源」。', click: 'resource-mode' },
  { id: 'formats', target: 'resource-formats', fallback: 'resource-mode', mode: 'resource', title: '选择你想要的学习形式', description: '讲解、互动教学、思维导图、PPT、实操和练习都可以选择。支持多选；不选时会根据你的要求判断。', task: '点击一种形式试试，例如「互动教学」。', event: 'format-selected' },
  { id: 'generate', target: 'composer', mode: 'resource', title: '把学习需求说具体', description: '告诉它学习主题，以及你希望看懂什么、操作什么。选中的形式会随这次请求一起发送。', task: '可以填入示例并发送，也可以先继续认识其他功能。', example: '请帮助初学者理解概率与频率的区别，用抛硬币的例子说明样本量增大时会发生什么。', event: 'resource-sent' },
  { id: 'workspace', target: 'resource-overview', view: 'resources', title: '在这里看见学习成果', description: '生成完成后，内容会出现在右侧。课件可以翻页，互动教学可以操作；多份资源可在上方切换。', task: '如果正在生成，可以等它完成后体验，也可以先继续引导。' },
  { id: 'reference', target: 'chat-reference', mode: 'chat', title: '围绕同一份作品继续学', description: '选中作品后，这里会显示引用的作品、版本和页面。可以问「为什么」，明确说「修改这一页」才会保存新版本；历史版本可以恢复。', task: '想检查理解时，可以在引用作品后说「考考我这一页」。' },
  { id: 'documents', target: 'document-tab', title: '带着自己的材料来学习', description: 'PDF、Word、Excel 和 Markdown 都有独立的原文阅读区，不用反复复制整篇内容。', task: '点击高亮的「文档原文」。', click: 'document-tab' },
  { id: 'document-read', target: 'document-upload', view: 'pdf', title: '选中哪段，就问哪段', description: '上传材料后，拖选正文或表格文字，左侧会引用准确内容。回答里的出处能跳回对应段落、页面或工作表位置。', task: '可上传自己的文档体验；暂时没有文件也可以继续。' },
  { id: 'keep', target: 'notes-entry', title: '把理解留在自己的笔记里', description: '阅读时选中文字，可通过右键菜单加入笔记。顶部「笔记」可回看整理的内容，「历史」可回到之前的对话。', task: '点击高亮入口结束引导并打开笔记，或继续下一步。', exitOnClick: 'notes-entry' },
  { id: 'record', target: 'learning-record', title: '回看真实的学习过程', description: '「学习记录」保留活动和作品。理解检查会区分独立作答、看过提示和重试，并保存判分与原文证据。', task: '可以点击高亮入口直接查看学习记录。', exitOnClick: 'learning-record' },
];

export function hasSeenTour(storage) {
  try { return Boolean(storage.getItem(TOUR_STORAGE_KEY)); } catch { return false; }
}
export function rememberTour(storage, outcome) {
  try { storage.setItem(TOUR_STORAGE_KEY, outcome); } catch { /* Session still works if storage is unavailable. */ }
}

// All coordinates are viewport coordinates. Pick a card position outside the hole
// when possible, then clamp for small windows or browser zoom.
export function tourGeometry(rect, viewport, card = { width: 340, height: 310 }) {
  const margin = 12, gap = 16;
  const width = Math.min(card.width, Math.max(0, viewport.width - margin * 2));
  const height = Math.min(card.height, Math.max(0, viewport.height - margin * 2));
  const clamp = (n, min, max) => Math.max(min, Math.min(n, max));
  const hole = rect ? {
    left: clamp(rect.left - 5, 0, viewport.width), top: clamp(rect.top - 5, 0, viewport.height),
    right: clamp(rect.right + 5, 0, viewport.width), bottom: clamp(rect.bottom + 5, 0, viewport.height),
  } : null;
  let left = (viewport.width - width) / 2, top = (viewport.height - height) / 2;
  if (hole) {
    if (viewport.width - hole.right >= width + gap + margin) { left = hole.right + gap; top = hole.top; }
    else if (hole.left >= width + gap + margin) { left = hole.left - gap - width; top = hole.top; }
    else if (viewport.height - hole.bottom >= height + gap + margin) { left = hole.left; top = hole.bottom + gap; }
    else if (hole.top >= height + gap + margin) { left = hole.left; top = hole.top - gap - height; }
    else { left = (viewport.width - width) / 2; top = viewport.height - height - margin; }
  }
  return { hole, card: { left: clamp(left, margin, viewport.width - width - margin), top: clamp(top, margin, viewport.height - height - margin), width } };
}
