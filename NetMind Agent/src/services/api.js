const API_BASE = import.meta.env.VITE_API_BASE || '';
const API_DISPLAY = API_BASE || '当前站点的 /api 代理';

async function requireResponse(res) {
  if (res.ok) return res;
  const payload = await res.json().catch(() => ({}));
  const detail = payload.detail || payload.message;
  throw new Error(typeof detail === 'string' ? detail : `请求失败（HTTP ${res.status}），请稍后重试。`);
}

export function uploadChatAttachments(files, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    Array.from(files || []).forEach(file => form.append('files', file));

    const request = new XMLHttpRequest();
    request.open('POST', `${API_BASE}/api/chat/attachments`);
    request.timeout = 10 * 60 * 1000;
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      onProgress?.({
        stage: 'uploading',
        progress: Math.max(2, Math.round((event.loaded / event.total) * 35)),
      });
    };
    request.upload.onload = () => {
      onProgress?.({ stage: 'processing', progress: 36 });
    };
    request.onerror = () => reject(new Error(
      `附件上传连接失败，未能访问${API_DISPLAY}。请确认后端仍在运行后重试。`,
    ));
    request.onabort = () => reject(new Error('附件上传已中断，请重新上传。'));
    request.ontimeout = () => reject(new Error('附件解析超过 10 分钟，已停止等待。'));
    request.onload = () => {
      if (request.status < 200 || request.status >= 300) {
        reject(new Error(request.responseText || `HTTP ${request.status}`));
        return;
      }
      try {
        onProgress?.({ stage: 'completed', progress: 100 });
        resolve(JSON.parse(request.responseText));
      } catch {
        reject(new Error('附件解析结果格式错误'));
      }
    };
    request.send(form);
  });
}

export async function deleteChatAttachment(attachmentId) {
  const res = await fetch(
    `${API_BASE}/api/chat/attachments/${encodeURIComponent(attachmentId)}`,
    { method: 'DELETE' },
  );
  if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
  return res.status === 404 ? { deleted: false } : res.json();
}

// ── 对话模式 ──────────────────────────────────────────

export async function sendChatMessageStream(
  message,
  studentId = 'default',
  history = [],
  handlers = {},
  userId = 'local_user',
  attachmentIds = [],
  resourceRef = null,
  pdfRef = null,
) {
  const res = await fetch(`${API_BASE}/api/chat/talk/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      student_id: studentId,
      user_id: userId,
      history,
      attachment_ids: attachmentIds,
      resource_ref: resourceRef,
      pdf_ref: pdfRef,
    }),
  });

  await requireResponse(res);
  if (!res.body) throw new Error('当前浏览器不支持流式响应');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let streamError = null;
  let completed = false;

  const dispatchEvent = (rawBlock) => {
    const block = rawBlock.trim();
    if (!block) return;

    let eventName = 'message';
    const dataLines = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length === 0) return;

    const rawData = dataLines.join('\n');
    let payload;
    try {
      payload = JSON.parse(rawData);
    } catch {
      payload = rawData;
    }
    if (eventName === 'done') completed = true;
    if (eventName === 'error') {
      streamError = new Error(payload?.message || '对话服务返回错误');
    }

    handlers.onEvent?.(eventName, payload);
    const handlerName = `on${eventName
      .split('_')
      .filter(Boolean)
      .map(part => part.charAt(0).toUpperCase() + part.slice(1))
      .join('')}`;
    handlers[handlerName]?.(payload);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() || '';
    parts.forEach(dispatchEvent);
  }

  buffer += decoder.decode();
  dispatchEvent(buffer);
  if (streamError) throw streamError;
  if (!completed) throw new Error('对话连接提前中断，作品可能已保存。请刷新核实作品版本后再操作。');
}

// ── 资源生成模式 ──────────────────────────────────────

export async function sendResourceMessageStream(
  message,
  studentId = 'default',
  handlers = {},
  userId = 'local_user',
  attachmentIds = [],
  requiredIntents = [],
  resourceRef = null,
) {
  const requestStartedAt = performance.now();
  const eventTimings = [];
  let completed = false;
  let streamError = null;
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      student_id: studentId,
      user_id: userId,
      attachment_ids: attachmentIds,
      required_intents: requiredIntents,
      resource_ref: resourceRef,
    }),
  });

  await requireResponse(res);
  if (!res.body) throw new Error('当前浏览器不支持流式响应');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatchEvent = (rawBlock) => {
    const block = rawBlock.trim();
    if (!block) return;

    let eventName = 'message';
    const dataLines = [];

    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    if (dataLines.length === 0) return;

    const rawData = dataLines.join('\n');
    let payload;
    try {
      payload = JSON.parse(rawData);
    } catch {
      payload = rawData;
    }

    const elapsedMs = Math.round(performance.now() - requestStartedAt);
    if (eventName !== 'resource_delta') {
      const timing = {
        event: eventName,
        elapsed_ms: elapsedMs,
        agent: payload?.agent || payload?.intent || '',
        resource_index: payload?.index ?? '',
      };
      eventTimings.push(timing);
      console.log(`[NetMind Timing][SSE] ${eventName}`, timing);
    }
    if (eventName === 'resource') {
      const diagnostics = payload?.resource?.metadata?.generation_diagnostics;
      if (diagnostics) {
        console.log('[NetMind Timing][PPT diagnostics]', diagnostics);
        console.table(diagnostics.timings_ms);
      }
    }
    if (eventName === 'error') streamError = new Error(payload?.message || '生成服务返回错误');
    if (eventName === 'done') {
      completed = true;
      console.groupCollapsed('[NetMind Timing] 资源生成耗时汇总');
      console.table(payload?.timings_ms || {});
      console.table(payload?.resource_timings_ms || []);
      console.table(eventTimings);
      console.groupEnd();
    }

    handlers.onEvent?.(eventName, payload);
    const handlerName = `on${eventName
      .split('_')
      .filter(Boolean)
      .map(part => part.charAt(0).toUpperCase() + part.slice(1))
      .join('')}`;
    handlers[handlerName]?.(payload);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() || '';
    parts.forEach(dispatchEvent);
  }

  buffer += decoder.decode();
  dispatchEvent(buffer);
  if (streamError) throw streamError;
  if (!completed) throw new Error('生成流未收到完成确认');
}

// ── 会话管理 ──────────────────────────────────────────

export async function getSessions(userId = 'local_user') {
  const res = await fetch(`${API_BASE}/api/sessions?user_id=${encodeURIComponent(userId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteSession(studentId, userId = 'local_user') {
  const res = await fetch(
    `${API_BASE}/api/sessions/${encodeURIComponent(studentId)}?user_id=${encodeURIComponent(userId)}`,
    { method: 'DELETE' },
  );
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getConversations(studentId, userId = 'local_user') {
  const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(studentId)}?user_id=${encodeURIComponent(userId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── 画像/资源 ─────────────────────────────────────────

export async function getProfile(studentId) {
  const res = await fetch(`${API_BASE}/api/profile/${studentId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getResources(studentId) {
  const res = await fetch(`${API_BASE}/api/resources/${studentId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function artifactQuery(studentId, userId, extra = {}) {
  return new URLSearchParams({ student_id: studentId, user_id: userId, ...extra }).toString();
}

export async function getArtifacts(studentId, userId = 'local_user', q = '') {
  const res = await fetch(`${API_BASE}/api/artifacts?${artifactQuery(studentId, userId, { q })}`);
  await requireResponse(res);
  return res.json();
}

export async function getArtifact(id, studentId, userId = 'local_user', version) {
  const extra = version == null ? {} : { version };
  const res = await fetch(`${API_BASE}/api/artifacts/${encodeURIComponent(id)}?${artifactQuery(studentId, userId, extra)}`);
  await requireResponse(res);
  return res.json();
}

export async function getArtifactSlides(id, studentId, userId = 'local_user', version) {
  const res = await fetch(`${API_BASE}/api/artifacts/${encodeURIComponent(id)}/slides?${artifactQuery(studentId, userId, { version })}`);
  await requireResponse(res);
  return res.json();
}

export async function getArtifactVersions(id, studentId, userId = 'local_user') {
  const res = await fetch(`${API_BASE}/api/artifacts/${encodeURIComponent(id)}/versions?${artifactQuery(studentId, userId)}`);
  await requireResponse(res);
  return res.json();
}

async function mutateArtifact(path, body) {
  const res = await fetch(`${API_BASE}/api/artifacts/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  await requireResponse(res);
  return res.json();
}

export function restoreArtifact(id, studentId, userId, version, expectedVersion, slideNumber = null) {
  return mutateArtifact(`${encodeURIComponent(id)}/restore`, {
    student_id: studentId, user_id: userId, version, expected_version: expectedVersion,
    ...(slideNumber == null ? {} : { slide_number: slideNumber }),
  });
}

export function reviseArtifact(id, studentId, userId, baseVersion, expectedVersion, instruction, selection = null) {
  return mutateArtifact(`${encodeURIComponent(id)}/revise`, {
    student_id: studentId, user_id: userId, base_version: baseVersion,
    expected_version: expectedVersion, instruction, ...(selection ? { selection } : {}),
  });
}

export function saveMessageArtifact(studentId, userId, conversationId, title) {
  return mutateArtifact('from-message', {
    student_id: studentId, user_id: userId, conversation_id: conversationId, ...(title ? { title } : {}),
  });
}

// ── 答题 ──────────────────────────────────────────────

export async function submitQuiz(studentId, quizId, userAnswer) {
  const res = await fetch(`${API_BASE}/api/quiz/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ student_id: studentId, quiz_id: quizId, user_answer: userAnswer }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function runTutorFollowup(studentId, quizId, userId = 'local_user') {
  const res = await fetch(`${API_BASE}/api/agent/followup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ student_id: studentId, user_id: userId, quiz_id: quizId }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getQuizRecords(studentId) {
  const res = await fetch(`${API_BASE}/api/quiz-records/${studentId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getUserSpace(userId = 'local_user') {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/space`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function recordLearningActivity(userId = 'local_user', studentId, seconds = 30) {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/activity`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ student_id: studentId, seconds }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function recordLearningEvent(userId = 'local_user', event = {}) {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(event),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getRemoteNotes(userId = 'local_user') {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/notes`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createRemoteNote(userId = 'local_user', note = {}) {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id: note.id,
      student_id: note.studentId,
      type: note.type,
      content: note.content,
      source: note.source,
      options: note.options || [],
      answer: note.answer,
      explanation: note.explanation,
      html: note.html,
      created_at: note.createdAt,
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function syncRemoteNotes(userId = 'local_user', studentId, notes = []) {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/notes/sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      student_id: studentId,
      notes: notes.map(note => ({
        id: note.id,
        student_id: note.studentId || studentId,
        type: note.type,
        content: note.content,
        source: note.source,
        options: note.options || [],
        answer: note.answer,
        explanation: note.explanation,
        html: note.html,
        created_at: note.createdAt,
      })),
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteRemoteNote(userId = 'local_user', noteId) {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/notes/${encodeURIComponent(noteId)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getAppSettings() {
  const res = await fetch(`${API_BASE}/api/settings`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function updateAppSettings(settings = {}) {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function uploadKnowledgeBase(files = []) {
  const formData = new FormData();
  Array.from(files).forEach(file => formData.append('files', file));
  const res = await fetch(`${API_BASE}/api/settings/knowledge-base/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getKnowledgeBaseJob(jobId) {
  const res = await fetch(`${API_BASE}/api/settings/knowledge-base/jobs/${encodeURIComponent(jobId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteKnowledgeBaseFile(filename) {
  const res = await fetch(`${API_BASE}/api/settings/knowledge-base/files/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function clearKnowledgeBaseFiles() {
  const res = await fetch(`${API_BASE}/api/settings/knowledge-base/files`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getResourceStorage() {
  const res = await fetch(`${API_BASE}/api/settings/storage`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function cleanupResourceStorage(days, resourceTypes = []) {
  const res = await fetch(`${API_BASE}/api/settings/storage/cleanup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      days,
      resource_types: resourceTypes,
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getPermanentMemory(userId = 'local_user') {
  const res = await fetch(`${API_BASE}/api/settings/memory/${encodeURIComponent(userId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function updatePermanentMemory(userId = 'local_user', memory = {}) {
  const res = await fetch(`${API_BASE}/api/settings/memory/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(memory),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
