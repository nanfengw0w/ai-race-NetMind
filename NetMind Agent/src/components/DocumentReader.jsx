import { useEffect, useRef, useState } from 'react';
import { getPdfLayout, getPdfPage } from '../services/documentApi';
import { selectionReference, spanHighlight, pdfLocationError } from '../utils/pdfSelection.mjs';
import { documentKindLabel, documentSelectionLabel } from '../utils/documentReference.mjs';
import styles from './DocumentReader.module.scss';

function SourceText({ span, location }) {
  const highlighted = spanHighlight(span, location);
  return <span data-pdf-start={span.start}>{highlighted ? <>{highlighted.before}<mark>{highlighted.selected}</mark>{highlighted.after}</> : span.text}</span>;
}

export default function DocumentReader({ document, studentId, userId, location, onSelectReference }) {
  const [manifest, setManifest] = useState(null);
  const [number, setNumber] = useState(1);
  const [loadedPage, setPage] = useState(null);
  const [layoutError, setLayoutError] = useState('');
  const [pageError, setPageError] = useState('');
  const [retry, setRetry] = useState(0);
  const root = useRef(null);
  const lastSelection = useRef(null);
  const lastSourceCommand = useRef(null);
  const pendingFocus = useRef(null);
  const page = loadedPage?.page_number === number ? loadedPage : null;
  const source = location?.document_id === document.id ? location : null;
  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => { if (!controller.signal.aborted) setLayoutError(''); });
    getPdfLayout(document.id, { studentId, userId, signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setManifest(value);
    }).catch(reason => { if (!controller.signal.aborted) setLayoutError(reason.message); });
    return () => controller.abort();
  }, [document.id, studentId, userId, retry]);
  useEffect(() => {
    const command = source?.nonce || source;
    if (!source || lastSourceCommand.current === command) return;
    lastSourceCommand.current = command;
    pendingFocus.current = source;
    const frame = requestAnimationFrame(() => setNumber(source.page_number));
    return () => cancelAnimationFrame(frame);
  }, [source]);
  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => { if (!controller.signal.aborted) { setPage(null); setPageError(''); } });
    getPdfPage(document.id, number, { studentId, userId, signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setPage(value);
    }).catch(reason => { if (!controller.signal.aborted) setPageError(reason.message); });
    return () => controller.abort();
  }, [document.id, number, studentId, userId, retry]);
  const locationError = pdfLocationError(page, source).replaceAll('PDF', '文档').replaceAll('原页', '原文');
  const activeLocation = page && !locationError && source?.page_number === number ? source : null;
  useEffect(() => {
    if (!page || !activeLocation || pendingFocus.current !== activeLocation) return;
    const frame = requestAnimationFrame(() => {
      root.current?.querySelector('mark')?.scrollIntoView({ block: 'center' });
      pendingFocus.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [page, activeLocation]);
  useEffect(() => {
    const owner = root.current?.ownerDocument;
    if (!owner || !page?.selectable) return;
    let timer;
    const publish = () => {
      const selected = owner.getSelection();
      if (!selected || selected.isCollapsed || selected.rangeCount !== 1) return;
      const range = selected.getRangeAt(0);
      if (!root.current?.contains(range.startContainer) || !root.current.contains(range.endContainer)) return;
      const reference = selectionReference(root.current, selected, page, document);
      if (!reference) return;
      const key = `${reference.page_number}:${reference.start}:${reference.end}`;
      if (key === lastSelection.current) return;
      lastSelection.current = key;
      onSelectReference?.({ ...reference, kind: page.kind || document.kind, location_label: documentSelectionLabel(page, reference) });
    };
    const schedule = event => { clearTimeout(timer); if (event.type === 'selectionchange') timer = setTimeout(publish, 80); else publish(); };
    owner.addEventListener('selectionchange', schedule);
    owner.addEventListener('pointerup', schedule);
    owner.addEventListener('keyup', schedule);
    return () => { clearTimeout(timer); owner.removeEventListener('selectionchange', schedule); owner.removeEventListener('pointerup', schedule); owner.removeEventListener('keyup', schedule); };
  }, [page, document, onSelectReference]);
  const units = manifest?.pages || [];
  const navigate = next => { pendingFocus.current = null; setNumber(next); };
  return <section className={styles.reader} aria-label="文档原文阅读">
    <header className={styles.toolbar}><strong title={document.title || document.name}>{document.title || document.name}</strong><span>{documentKindLabel(document)}</span>
      <button aria-label="上一部分" disabled={number <= 1 || !units.length} onClick={() => navigate(number - 1)}>‹</button>
      <select aria-label="章节或工作表" value={number} onChange={event => navigate(Number(event.target.value))}>{units.map(unit => <option key={unit.page_number} value={unit.page_number}>{unit.label || `第 ${unit.page_number} 部分`}</option>)}</select>
      <button aria-label="下一部分" disabled={number >= units.length} onClick={() => navigate(number + 1)}>›</button>
    </header>
    <div className={styles.hint}>拖选当前部分的原文向左侧提问。{['xlsx', 'xls', 'csv'].includes(document.kind) && '表格可横向滚动；单元格地址显示在文字上方。'}</div>
    {document.warning && <div className={styles.hint}>{document.warning}</div>}
    {layoutError && <div className={styles.error} role="alert">目录加载失败：{layoutError} <button onClick={() => setRetry(value => value + 1)}>重试</button></div>}
    {locationError && <div className={styles.error} role="alert">{locationError}</div>}
    <div className={styles.viewport}>
      {pageError ? <div className={styles.error} role="alert">{pageError} <button onClick={() => setRetry(value => value + 1)}>重试</button></div> : !page ? <div className={styles.status} role="status">正在读取文档原文…</div> : <>
        <div className={styles.unitLabel}>{page.label}</div>
        {!page.selectable && <div className={styles.status}>此部分没有可选文字。</div>}
        <article ref={root} className={styles.source} key={`${document.id}:${number}`} onPointerDown={() => { lastSelection.current = null; }}>
          {(page.blocks?.length ? page.blocks : page.text ? [{ type: 'paragraph', text: page.text, start: 0, end: Array.from(page.text).length }] : []).map((block, index) => block.type === 'table' ? <div key={index} className={styles.tableScroll}><table><tbody>{block.rows?.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell.address && <div className={styles.address} contentEditable={false}>{cell.address}</div>}<SourceText span={cell} location={activeLocation} /></td>)}</tr>)}</tbody></table></div> : <div key={index} className={block.type === 'heading' ? styles.heading : styles.paragraph}><SourceText span={block} location={activeLocation} /></div>)}
        </article>
      </>}
    </div>
  </section>;
}

