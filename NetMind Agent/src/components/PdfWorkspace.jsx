import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import PdfReader from './PdfReader';
import DocumentReader from './DocumentReader';
import { getPdfDocument, listPdfDocuments, uploadPdf } from '../services/documentApi';
import styles from './PdfWorkspace.module.scss';

const PdfWorkspace = forwardRef(function PdfWorkspace({ studentId, userId, location, onSelectReference, onUploaded }, ref) {
  const [documents, setDocuments] = useState([]);
  const [activeDocument, setActiveDocument] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const fileInput = useRef(null);
  const mounted = useRef(false);
  const generation = useRef(0);
  const sourceGeneration = useRef(0);
  const uploadingRef = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const requestGeneration = ++generation.current;
    listPdfDocuments({ studentId, userId, signal: controller.signal }).then(data => {
      if (!mounted.current || generation.current !== requestGeneration) return;
      setDocuments(data);
      setActiveDocument(current => current || data[0] || null);
      setError('');
    }).catch(reason => {
      if (!mounted.current || controller.signal.aborted || generation.current !== requestGeneration) return;
      if (reason.status !== 404) setError(reason.message);
    }).finally(() => { if (mounted.current && !controller.signal.aborted) setLoading(false); });
    return () => { mounted.current = false; controller.abort(); };
  }, [studentId, userId, retry]);
  const upload = useCallback(async file => {
    if (uploadingRef.current) throw new Error('正在上传文档，请稍候。');
    uploadingRef.current = true;
    generation.current += 1;
    sourceGeneration.current += 1;
    setUploading(true); setError('');
    try {
      const document = await uploadPdf(file, { studentId, userId });
      if (mounted.current) {
        setDocuments(current => [document, ...current.filter(item => item.id !== document.id)]);
        setActiveDocument(document);
        setLoading(false);
      }
      onUploaded?.(document);
      return document;
    } catch (reason) {
      if (mounted.current) setError(reason.message);
      throw reason;
    } finally {
      uploadingRef.current = false;
      if (mounted.current) setUploading(false);
    }
  }, [studentId, userId, onUploaded]);
  const openSource = useCallback(async citation => {
    setError('');
    const requestGeneration = ++sourceGeneration.current;
    try {
      const document = documents.find(item => item.id === citation.document_id)
        || await getPdfDocument(citation.document_id, { studentId, userId });
      if (mounted.current && requestGeneration === sourceGeneration.current) {
        setDocuments(current => current.some(item => item.id === document.id) ? current : [...current, document]);
        setActiveDocument(document);
      }
    } catch (reason) { if (mounted.current) setError(reason.message); }
  }, [documents, studentId, userId]);
  useImperativeHandle(ref, () => ({ upload, openSource }), [upload, openSource]);
  return <section className={styles.workspace} aria-label="文档原文">
    <div className={styles.documents}>
      <label>当前对话文档 <select aria-label="当前文档" value={activeDocument?.id || ''} onChange={event => { sourceGeneration.current += 1; setActiveDocument(documents.find(item => item.id === event.target.value) || null); }}>
        {!documents.length && <option value="">暂无文档</option>}
        {documents.map(document => <option key={document.id} value={document.id}>{document.title || document.name}</option>)}
      </select></label>
      <button data-tour="document-upload" disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? '上传中…' : '上传文档'}</button>
      <input ref={fileInput} hidden type="file" accept=".pdf,.docx,.xlsx,.xls,.csv,.md,.markdown,.txt" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) upload(file).catch(() => {}); }} />
    </div>
    {error && <div className={styles.error} role="alert">{error} <button onClick={() => setRetry(value => value + 1)}>重试列表</button></div>}
    <div className={styles.content}>
      {activeDocument ? (activeDocument.kind && activeDocument.kind !== 'pdf' ? <DocumentReader key={activeDocument.id} document={activeDocument} studentId={studentId} userId={userId} location={location} onSelectReference={onSelectReference} /> : <PdfReader key={activeDocument.id} document={activeDocument} studentId={studentId} userId={userId} location={location} onSelectReference={onSelectReference} />)
        : <div className={styles.empty}>{loading ? '正在读取文档列表…' : '上传文档，支持 PDF、Word、Excel、CSV、Markdown 和文本，拖选原文向左侧提问。'}</div>}
    </div>
  </section>;
});
export default PdfWorkspace;

