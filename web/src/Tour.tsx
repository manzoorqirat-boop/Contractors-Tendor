import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { tr } from './i18n';

const KEY = 'tour-done';

interface Step { target: string | null; title: string; text: string }

/** A short walk round the menu for someone who has just arrived. The target is a `data-tour` attribute on a menu item; when it is not on screen (the phone folds some items away) the step is shown in the middle instead. */
const STEPS: Step[] = [
  { target: null, title: tr('Welcome to TendAI'), text: tr('A 30-second look round: where things are, and what each part is for. You can skip it, and run it again from Help.') },
  { target: 'dashboard', title: tr('Dashboard'), text: tr('Start here each day: what closes soon, money locked up in EMDs, and what is new.') },
  { target: 'tenders', title: tr('Tenders'), text: tr('Every tender collected from the portals. Search in plain words, filter, and star the good ones.') },
  { target: 'bids', title: tr('Bids'), text: tr('The tenders you are working on, each with its checklist, price tools and deadline reminders.') },
  { target: 'vault', title: tr('Vault'), text: tr('Your standard papers (GST, PAN, registration, balance sheets). Bids match their checklists to these.') },
  { target: 'help', title: tr('Help'), text: tr('A step-by-step guide, where to find things, and what the words mean. Press ? any time for keyboard shortcuts.') },
];

function visible(el: Element | null): el is HTMLElement {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
}

/** Whether to start the tour by itself: once per browser, never inside automated browsers (tests must not meet an overlay). */
export function shouldAutoStart(): boolean {
  try { if (localStorage.getItem(KEY) === '1') return false; } catch { return false; }
  return !(typeof navigator !== 'undefined' && navigator.webdriver);
}

export function markTourDone() { try { localStorage.setItem(KEY, '1'); } catch { /* ignore */ } }

/** The tour's open state: starts by itself the first time, and again whenever something sends the "open-tour" event (Help does). */
export function useTour(ready: boolean): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!ready || !shouldAutoStart()) return;
    const t = setTimeout(() => { if (!document.querySelector('[role=dialog], [role=alertdialog]')) setOpen(true); }, 1200);
    return () => clearTimeout(t);
  }, [ready]);
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener('open-tour', show);
    return () => window.removeEventListener('open-tour', show);
  }, []);
  return [open, setOpen];
}

export function Tour({ onClose }: { onClose: () => void }) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const step = STEPS[i];
  const last = i === STEPS.length - 1;
  const finish = () => { markTourDone(); onClose(); };

  // Find the thing the step is about (and keep following it if the window changes size)
  useLayoutEffect(() => {
    const find = () => {
      const el = step.target ? document.querySelector(`[data-tour="${step.target}"]`) : null;
      setRect(visible(el) ? el.getBoundingClientRect() : null);
    };
    find();
    window.addEventListener('resize', find);
    return () => window.removeEventListener('resize', find);
  }, [step.target]);

  useEffect(() => { card.current?.focus(); }, [i]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(); }
      if (e.key === 'ArrowRight' && !last) setI((n) => n + 1);
      if (e.key === 'ArrowLeft' && i > 0) setI((n) => n - 1);
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  });

  const narrow = window.innerWidth < 700;
  // next to the highlighted item (right of the side menu on a wide screen, above the bottom bar on a phone); in the middle when there is nothing to point at
  const place: React.CSSProperties = !rect ? { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }
    : narrow ? { left: 12, right: 12, bottom: Math.max(12, window.innerHeight - rect.top + 12) }
    : { left: Math.min(rect.right + 16, window.innerWidth - 340), top: Math.max(12, Math.min(rect.top - 8, window.innerHeight - 220)) };

  return (
    <div className="tour">
      <div className={`tour-shade${rect ? ' clear' : ''}`} onClick={finish} />
      {rect && <div className="tour-ring" style={{ left: rect.left - 4, top: rect.top - 4, width: rect.width + 8, height: rect.height + 8 }} aria-hidden />}
      <div className="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" tabIndex={-1} ref={card} style={place}>
        <div className="muted small" aria-live="polite">{tr('{n} of {total}', { n: i + 1, total: STEPS.length })}</div>
        <h2 id="tour-title" style={{ margin: '4px 0 6px', fontSize: 'var(--fs-lg)' }}>{step.title}</h2>
        <p style={{ margin: '0 0 12px' }}>{step.text}</p>
        <div className="tour-actions">
          <button type="button" className="link-btn" onClick={finish}>{last ? tr('Close') : tr('Skip the tour')}</button>
          <span className="spacer" />
          {i > 0 && <button type="button" className="btn small" onClick={() => setI(i - 1)}>{tr('Back')}</button>}
          <button type="button" className="btn small primary" onClick={() => (last ? finish() : setI(i + 1))}>{last ? tr('Done') : tr('Next')}</button>
        </div>
      </div>
    </div>
  );
}
