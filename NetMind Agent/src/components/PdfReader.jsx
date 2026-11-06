import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { getPdfLayout, getPdfPage, getPdfPageImageUrl } from '../services/documentApi';
import { selectionReference, spanHighlight, pdfLocationError } from '../utils/pdfSelection.mjs';
import { buildPdfLayout, pageAtOffset, visiblePdfPages, remapPdfScroll, PDF_PADDING } from '../utils/pdfLayout.mjs';
import styles from './PdfReader.module.scss';

const EMPTY_PAGES = [];
const PdfPage = memo(function PdfPage({ documentId, pageNumber, studentId, userId, scale, location, cache, onRegister, onFocusSource }) {
  const key = `${documentId}:${pageNumber}`;
  const [page, setPage] = useState(() => cache.current.get(key) || null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [imageError, setImageError] = useState(false);
  const layer = useRef(null);
  useEffect(() => {
    const cached = cache.current.get(key);
    if (cached && retry === 0) return;
    const controller = new AbortController();
    getPdfPage(documentId, pageNumber, { studentId, userId, signal: controller.signal })
      .then(value => {
        if (controller.signal.aborted) return;
        cache.current.delete(key);
        cache.current.set(key, value);
        while (cache.current.size > 8) cache.current.delete(cache.current.keys().next().value);
        setPage(value);
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [documentId, pageNumber, studentId, userId, key, retry, cache]);
  const locationError = pdfLocationError(page, location);
  const activeLocation = locationError ? null : location;
  useEffect(() => {
    if (!page || imageError) return;
    onRegister(pageNumber, page, layer.current);
    return () => onRegister(pageNumber, null, null);
  }, [pageNumber, page, imageError, onRegister]);
  useEffect(() => {
    if (!activeLocation || !page || imageError) return;
    const frame = requestAnimationFrame(() => onFocusSource(activeLocation, layer.current?.querySelector('mark')));
    return () => cancelAnimationFrame(frame);
  }, [activeLocation, page, imageError, scale, onFocusSource]);
  const tryAgain = () => { setError(''); setImageError(false); setRetry(value => value + 1); };
  if (error) return <div className={styles.status} role="alert">第 {pageNumber} 页：{error} <button onClick={tryAgain}>重试本页</button></div>;
  if (!page) return <div className={styles.status} role="status">正在加载第 {pageNumber} 页…</div>;
  return <>
    <img key={retry} alt={`PDF 第 ${pageNumber} 页原页`} draggable="false" src={getPdfPageImageUrl(documentId, pageNumber, { studentId, userId })} onError={() => setImageError(true)} />
    {imageError && <div className={styles.pageNotice} role="alert">原页图像加载失败 <button onClick={tryAgain}>重试本页</button></div>}
    {locationError && <div className={styles.pageNotice} role="alert">{locationError}</div>}
    {!page.selectable && <div className={styles.pageNotice}>此页没有可选文字，可继续阅读原页。</div>}
    {!imageError && <div ref={layer} data-pdf-page={pageNumber} className={styles.textLayer} style={{ width: page.width, height: page.height, transform: `scale(${scale})` }}>
      {(page.chars?.length ? page.chars.filter(char => char.bbox) : page.spans || []).map((span, index) => {
        const highlighted = spanHighlight(span, activeLocation);
        return <span key={index} data-pdf-start={span.start} className={styles.textSpan} style={{ left: span.bbox[0], top: span.bbox[1], fontSize: span.font_size || span.bbox[3] - span.bbox[1], height: span.bbox[3] - span.bbox[1] }} ref={element => {
          if (!element) return;
          element.style.transform = 'none';
          const naturalWidth = element.getBoundingClientRect().width / scale;
          element.style.transform = `scaleX(${naturalWidth ? (span.bbox[2] - span.bbox[0]) / naturalWidth : 1})`;
        }}>
          {highlighted ? <>{highlighted.before}<mark>{highlighted.selected}</mark>{highlighted.after}</> : span.text}
        </span>;
      })}
    </div>}
  </>;
});

export default function PdfReader({ studentId, userId, document, onSelectReference, onClose, location }) {
  const [manifest, setManifest] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [view, setView] = useState({ width: 800, height: 0, top: 0 });
  const [pageInput, setPageInput] = useState(null);
  const [selectionNotice, setSelectionNotice] = useState('');
  const viewport = useRef(null);
  const cache = useRef(new Map());
  const mountedPages = useRef(new Map());
  const previousLayout = useRef(null);
  const pendingSource = useRef(null);
  const lastSourceCommand = useRef(null);
  const lastSelection = useRef(null);
  const scrollFrame = useRef(0);
  const documentId = document?.id;
  useEffect(() => {
    const controller = new AbortController();
    getPdfLayout(documentId, { studentId, userId, signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setManifest(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [documentId, studentId, userId, retry]);
  const pages = manifest?.document_id === documentId ? manifest.pages : EMPTY_PAGES;
  const layout = useMemo(() => buildPdfLayout(pages, view.width, zoom), [pages, view.width, zoom]);
  const sourceLocation = location?.document_id === documentId ? location : null;
  const totalPages = pages.length || document?.page_count || 1;
  const currentIndex = pageAtOffset(layout.items, view.top - PDF_PADDING + view.height / 2);
  const pageNumber = layout.items[currentIndex]?.number || 1;
  const visiblePages = view.height ? visiblePdfPages(layout.items, view.top - PDF_PADDING, view.height) : new Set();
  const measure = useCallback(() => {
    const element = viewport.current;
    if (!element) return;
    if (!element.clientWidth || !element.clientHeight) {
      setView(previous => previous.height ? { ...previous, height: 0 } : previous);
      return;
    }
    const next = { width: Math.max(160, element.clientWidth - PDF_PADDING * 2), height: element.clientHeight, top: element.scrollTop };
    setView(previous => previous.width === next.width && previous.height === next.height && previous.top === next.top ? previous : next);
  }, []);
  useEffect(() => {
    const observer = new ResizeObserver(measure);
    if (viewport.current) observer.observe(viewport.current);
    return () => { observer.disconnect(); cancelAnimationFrame(scrollFrame.current); };
  }, [measure]);
  useLayoutEffect(() => {
    const element = viewport.current;
    const old = previousLayout.current;
    previousLayout.current = layout;
    if (old?.items.length && layout.items.length && element) {
      element.scrollTop = PDF_PADDING + remapPdfScroll(old.items, layout.items, element.scrollTop - PDF_PADDING);
    }
    measure();
  }, [layout, measure]);
  const scrollToPage = useCallback(number => {
    const item = layout.items[number - 1];
    if (!item || !viewport.current) return;
    viewport.current.scrollTop = item.top + PDF_PADDING;
    measure();
  }, [layout, measure]);
  useEffect(() => {
    if (!sourceLocation || !layout.items.length || !view.height || lastSourceCommand.current === sourceLocation) return;
    lastSourceCommand.current = sourceLocation;
    pendingSource.current = sourceLocation;
    scrollToPage(sourceLocation.page_number);
  }, [sourceLocation, scrollToPage, layout.items.length, view.height]);
  const focusSource = useCallback((source, element) => {
    if (pendingSource.current !== source || !element || !viewport.current) return;
    pendingSource.current = null;
    const container = viewport.current;
    container.scrollTop += element.getBoundingClientRect().top - container.getBoundingClientRect().top - container.clientHeight / 2;
    measure();
  }, [measure]);
  const registerPage = useCallback((number, page, layer) => {
    if (page && layer) mountedPages.current.set(number, { page, layer });
    else mountedPages.current.delete(number);
  }, []);
  useEffect(() => {
    const owner = viewport.current?.ownerDocument;
    if (!owner) return;
    let timer;
    const publishSelection = () => {
      const selected = owner.getSelection();
      if (!selected || selected.isCollapsed || selected.rangeCount !== 1) return;
      const range = selected.getRangeAt(0);
      const getLayer = node => (node.nodeType === 1 ? node : node.parentElement)?.closest('[data-pdf-page]');
      const start = getLayer(range.startContainer);
      const end = getLayer(range.endContainer);
      if (!start || !end || !viewport.current?.contains(start) || !viewport.current.contains(end)) return;
      if (start !== end) {
        setSelectionNotice('请在同一页内选择要引用的文字。跨页选区不会作为引用发送。');
        if (lastSelection.current !== 'cross-page') onSelectReference?.(null);
        lastSelection.current = 'cross-page';
        return;
      }
      const loaded = mountedPages.current.get(Number(start.dataset.pdfPage));
      if (!loaded) return;
      const reference = selectionReference(loaded.layer, selected, loaded.page, document);
      if (!reference) return;
      const key = `${reference.document_id}:${reference.page_number}:${reference.start}:${reference.end}`;
      setSelectionNotice('');
      if (lastSelection.current === key) return;
      lastSelection.current = key;
      onSelectReference?.(reference);
    };
    const schedule = event => { clearTimeout(timer); timer = setTimeout(publishSelection, event.type === 'selectionchange' ? 80 : 0); };
    owner.addEventListener('selectionchange', schedule);
    owner.addEventListener('pointerup', schedule);
    owner.addEventListener('keyup', schedule);
    return () => {
      clearTimeout(timer);
      owner.removeEventListener('selectionchange', schedule);
      owner.removeEventListener('pointerup', schedule);
      owner.removeEventListener('keyup', schedule);
    };
  }, [document, onSelectReference]);
  const navigate = number => { pendingSource.current = null; setPageInput(null); scrollToPage(Math.max(1, Math.min(totalPages, number))); };
  const commitPage = () => { if (pageInput !== null) navigate(Number(pageInput) || pageNumber); };
  return <section className={styles.reader} aria-label="PDF 原页阅读">
    <header className={styles.toolbar}>
      <strong title={document?.title || document?.name}>{document?.title || document?.name || 'PDF 原文'}</strong>
      <div className={styles.controls}>
        <button disabled={pageNumber <= 1 || !pages.length} onClick={() => navigate(pageNumber - 1)} aria-label="上一页">‹</button>
        <label><input aria-label="页码" title="输入页码后按回车跳转" type="number" min="1" max={totalPages} value={pageInput ?? pageNumber} onChange={event => setPageInput(event.target.value)} onBlur={commitPage} onKeyDown={event => { if (event.key === 'Enter') commitPage(); }} /> / {totalPages}</label>
        <button disabled={pageNumber >= totalPages || !pages.length} onClick={() => navigate(pageNumber + 1)} aria-label="下一页">›</button>
        <button onClick={() => setZoom(value => Math.max(.5, value - .25))} disabled={zoom <= .5} aria-label="缩小">−</button>
        <button className={styles.zoom} onClick={() => setZoom(1)} title="恢复适合宽度">{Math.round(zoom * 100)}%</button>
        <button onClick={() => setZoom(value => Math.min(3, value + .25))} disabled={zoom >= 3} aria-label="放大">＋</button>
        {onClose && <button onClick={onClose} aria-label="关闭 PDF">×</button>}
      </div>
    </header>
    <div className={styles.hint}>向下滚动连续阅读，拖选同一页内的文字向左侧提问。</div>
    {selectionNotice && <div className={styles.locationError} role="status">{selectionNotice}</div>}
    <div className={styles.viewport} ref={viewport} aria-label="PDF 连续阅读区" tabIndex={0}
      onScroll={() => { cancelAnimationFrame(scrollFrame.current); scrollFrame.current = requestAnimationFrame(measure); }}
      onWheel={() => { pendingSource.current = null; }} onTouchStart={() => { pendingSource.current = null; }}
      onPointerDown={() => { pendingSource.current = null; lastSelection.current = null; }}>
      {error ? <div className={styles.status} role="alert">{error} <button onClick={() => { setError(''); setRetry(value => value + 1); }}>重试</button></div>
        : !pages.length ? <div className={styles.status} role="status">正在加载 PDF 页面布局…</div>
          : <div className={styles.document} style={{ height: layout.height, width: layout.width }}>
            {layout.items.map(item => <section key={item.number} className={styles.pageSlot} aria-label={`PDF 第 ${item.number} 页`} style={{ top: item.top, left: (layout.width - item.width) / 2, width: item.width }}>
              <div className={styles.pageLabel}>第 {item.number} 页</div>
              <div className={styles.page} style={{ height: item.height }}>
                {visiblePages.has(item.number) ? <PdfPage documentId={documentId} pageNumber={item.number} studentId={studentId} userId={userId} scale={item.scale}
                  location={sourceLocation?.page_number === item.number ? sourceLocation : null} cache={cache} onRegister={registerPage} onFocusSource={focusSource} />
                  : <div className={styles.placeholder}>第 {item.number} 页</div>}
              </div>
            </section>)}
          </div>}
    </div>
  </section>;
}
