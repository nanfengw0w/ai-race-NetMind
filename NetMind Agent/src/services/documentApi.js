const API_BASE = import.meta.env.VITE_API_BASE || '';
const identityQuery = ({ studentId, userId } = {}) => new URLSearchParams({ student_id: studentId || '', user_id: userId || '' }).toString();
async function readResponse(response) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(typeof data.detail === 'string' ? data.detail : `文档请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}
export function getPdfPageImageUrl(documentId, pageNumber, identity) {
  return `${API_BASE}/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}/image?${identityQuery(identity)}`;
}
export async function getPdfPage(documentId, pageNumber, options = {}) {
  return readResponse(await fetch(`${API_BASE}/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}?${identityQuery(options)}`, { signal: options.signal }));
}
export async function getPdfLayout(documentId, options = {}) {
  return readResponse(await fetch(`${API_BASE}/api/documents/${encodeURIComponent(documentId)}/layout?${identityQuery(options)}`, { signal: options.signal }));
}
export async function listPdfDocuments(options = {}) {
  return readResponse(await fetch(`${API_BASE}/api/documents?${identityQuery(options)}`, { signal: options.signal }));
}
export async function uploadPdf(file, { studentId, userId, signal } = {}) {
  const form = new FormData();
  form.append('file', file);
  form.append('student_id', studentId || '');
  form.append('user_id', userId || '');
  return readResponse(await fetch(`${API_BASE}/api/documents/upload`, { method: 'POST', body: form, signal }));
}

export async function getPdfDocument(documentId, options = {}) {
  return readResponse(await fetch(`${API_BASE}/api/documents/${encodeURIComponent(documentId)}?${identityQuery(options)}`, { signal: options.signal }));
}


