import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { TOUR_STEPS, hasSeenTour, rememberTour, tourGeometry } from '../utils/onboarding.mjs';
import styles from './OnboardingTour.module.scss';

const focusable = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]';
const isVisible = element => element && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
const storage = () => { try { return window.localStorage; } catch { return null; } };
function clippedRect(element) {
  if (!element) return null;
  const bounds = element.getBoundingClientRect();
  const rect = { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom };
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    const css = getComputedStyle(parent);
    const box = parent.getBoundingClientRect();
    if (/(auto|scroll|hidden|clip)/.test(css.overflowX)) { rect.left = Math.max(rect.left, box.left); rect.right = Math.min(rect.right, box.right); }
    if (/(auto|scroll|hidden|clip)/.test(css.overflowY)) { rect.top = Math.max(rect.top, box.top); rect.bottom = Math.min(rect.bottom, box.bottom); }
  }
  return rect.right > rect.left && rect.bottom > rect.top ? rect : null;
}

const OnboardingTour = forwardRef(function OnboardingTour({ onPrepare, onExample }, ref) {
  const [stage, setStage] = useState(() => hasSeenTour(storage()) ? 'closed' : 'welcome');
  const [index, setIndex] = useState(0);
  const [geometry, setGeometry] = useState(null);
  const [notice, setNotice] = useState('');
  const cardRef = useRef(null);
  const anchorRef = useRef(null);
  const returnFocus = useRef(null);
  const step = TOUR_STEPS[index];
  const active = stage !== 'closed';

  const close = useCallback(outcome => {
    rememberTour(storage(), outcome);
    setStage('closed');
    requestAnimationFrame(() => {
      const target = returnFocus.current?.isConnected ? returnFocus.current : document.querySelector('[data-tour="restart"]');
      target?.focus();
    });
  }, []);
  const enter = useCallback(next => {
    setNotice('');
    setGeometry(null);
    if (next >= TOUR_STEPS.length) { setStage('finish'); return; }
    onPrepare?.(TOUR_STEPS[next]);
    setIndex(next);
    setStage('step');
  }, [onPrepare]);
  const next = useCallback(() => enter(index + 1), [enter, index]);
  useImperativeHandle(ref, () => ({
    open() {
      returnFocus.current = document.activeElement;
      setNotice(''); setGeometry(null); setIndex(0); setStage('welcome');
    },
    event(name) { if (stage === 'step' && step.event === name) next(); },
  }), [stage, step, next]);

  useLayoutEffect(() => {
    if (!active) return;
    let frame = 0;
    const measure = () => {
      const find = name => [...document.querySelectorAll(`[data-tour="${name}"]`)].find(isVisible);
      const element = stage === 'step' ? find(step.target) || (step.fallback && find(step.fallback)) : null;
      anchorRef.current = element || null;
      let rect = clippedRect(element);
      if (element && (!rect || rect.bottom <= 0 || rect.top >= window.innerHeight || rect.right <= 0 || rect.left >= window.innerWidth)) {
        element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
        rect = clippedRect(element);
      }
      const inView = rect && rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
      const card = cardRef.current?.getBoundingClientRect();
      const value = tourGeometry(inView ? rect : null, { width: window.innerWidth, height: window.innerHeight }, { width: 340, height: card?.height || 310 });
      value.found = Boolean(inView);
      setGeometry(previous => JSON.stringify(previous) === JSON.stringify(value) ? previous : value);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    const target = [...document.querySelectorAll(`[data-tour="${step.target}"]`)].find(isVisible);
    if (stage === 'step') target?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
    measure();
    const observer = new ResizeObserver(schedule);
    observer.observe(document.documentElement);
    if (cardRef.current) observer.observe(cardRef.current);
    if (target) observer.observe(target);
    const mutations = new MutationObserver(schedule);
    const root = document.getElementById('root');
    if (root) mutations.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'class', 'style'] });
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, true);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); mutations.disconnect(); window.removeEventListener('resize', schedule); window.removeEventListener('scroll', schedule, true); };
  }, [active, stage, step]);

  useEffect(() => {
    if (!active) return;
    cardRef.current?.focus({ preventScroll: true });
    const keydown = event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close('skipped'); }
      if (event.key !== 'Tab') return;
      const anchor = anchorRef.current;
      const allowed = [
        ...(isVisible(anchor) ? [...(anchor.matches(focusable) ? [anchor] : []), ...anchor.querySelectorAll(focusable)] : []),
        ...cardRef.current.querySelectorAll(focusable),
      ].filter(isVisible);
      if (!allowed.length) return;
      event.preventDefault();
      const current = allowed.indexOf(document.activeElement);
      allowed[(current + (event.shiftKey ? -1 : 1) + allowed.length) % allowed.length].focus();
    };
    const focusin = event => {
      if (cardRef.current?.contains(event.target) || anchorRef.current?.contains(event.target)) return;
      cardRef.current?.focus({ preventScroll: true });
    };
    const click = event => {
      if (stage === 'step' && step.exitOnClick && event.target.closest?.(`[data-tour="${step.exitOnClick}"]`)) { close('exploring'); return; }
      if (stage === 'step' && step.click && event.target.closest?.(`[data-tour="${step.click}"]`) && !event.target.closest(':disabled')) next();
    };
    document.addEventListener('keydown', keydown, true);
    document.addEventListener('focusin', focusin);
    document.addEventListener('click', click);
    return () => { document.removeEventListener('keydown', keydown, true); document.removeEventListener('focusin', focusin); document.removeEventListener('click', click); };
  }, [active, stage, step, next, close]);

  if (!active) return null;
  const hole = stage === 'step' ? geometry?.hole : null;
  const fillExample = () => {
    const result = onExample?.(step.example);
    setNotice(result?.ok ? '示例已填入。你可以修改，再自行发送。' : result?.reason || '输入区暂不可用，请稍后再试。');
  };
  return createPortal(<div className={styles.tour} data-testid="onboarding-tour">
    {hole ? <>
      <div className={styles.shade} style={{ top: 0, left: 0, right: 0, height: hole.top }} />
      <div className={styles.shade} style={{ top: hole.bottom, left: 0, right: 0, bottom: 0 }} />
      <div className={styles.shade} style={{ top: hole.top, left: 0, width: hole.left, height: hole.bottom - hole.top }} />
      <div className={styles.shade} style={{ top: hole.top, left: hole.right, right: 0, height: hole.bottom - hole.top }} />
      <div className={styles.spotlight} style={{ left: hole.left, top: hole.top, width: hole.right - hole.left, height: hole.bottom - hole.top }} />
    </> : <div className={styles.shade} style={{ inset: 0 }} />}
    <section ref={cardRef} className={styles.card} style={geometry?.card} role="dialog" aria-label="新手引导" aria-describedby="tour-description" tabIndex={-1}>
      <div className={styles.cardTop}><span>NETMIND · 新手引导</span><button type="button" aria-label="退出新手引导" onClick={() => close('skipped')}>×</button></div>
      {stage === 'welcome' ? <>
        <span className={styles.kicker}>从疑问，到理解</span>
        <h2>一起走一遍？</h2>
        <p id="tour-description">跟着高亮区域，认识提问、生成资源、作品引用和文档阅读。你可以边操作边体验，也可以随时跳过。</p>
        <div className={styles.route}><span>01 提出问题</span><span>02 动手探索</span><span>03 留下理解</span></div>
        <p className={styles.subtle}>示例由你确认填入和发送，已有草稿会保留。</p>
        <footer><button type="button" onClick={() => close('skipped')}>暂时跳过</button><button type="button" className={styles.primary} onClick={() => enter(0)}>开始体验 →</button></footer>
      </> : stage === 'finish' ? <>
        <span className={styles.kicker}>入口已经认识，探索才刚开始</span>
        <h2>带着你的问题出发</h2>
        <p id="tour-description">现在可以选一个真实问题，试着生成讲解、操作互动内容，或上传一份正在阅读的材料。</p>
        <p className={styles.subtle}>以后点击顶部「新手引导」，随时重新走一遍。</p>
        <footer><button type="button" onClick={() => enter(0)}>再走一遍</button><button type="button" className={styles.primary} onClick={() => close('completed')}>开始学习</button></footer>
      </> : <>
        <div className={styles.progress} aria-label={`第 ${index + 1} 步，共 ${TOUR_STEPS.length} 步`}><span style={{ width: `${(index + 1) / TOUR_STEPS.length * 100}%` }} /></div>
        <span className={styles.kicker}>{String(index + 1).padStart(2, '0')} / {TOUR_STEPS.length}</span>
        <h2>{step.title}</h2>
        <p id="tour-description">{step.description}</p>
        <div className={styles.task}>{step.task}</div>
        {geometry && !geometry.found && <p className={styles.subtle}>这个入口暂未显示，可以继续下一步，稍后再体验。</p>}
        {step.example && <button className={styles.example} type="button" onClick={fillExample}>填入示例问题 ↗</button>}
        {notice && <p className={styles.notice} role="status">{notice}</p>}
        <footer><button type="button" onClick={() => close('skipped')}>跳过引导</button><div><button type="button" disabled={!index} onClick={() => enter(index - 1)}>上一步</button><button type="button" className={styles.primary} onClick={next}>{index === TOUR_STEPS.length - 1 ? '完成引导' : '下一步'} →</button></div></footer>
      </>}
    </section>
  </div>, document.body);
});
export default OnboardingTour;
