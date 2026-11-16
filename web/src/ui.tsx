import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';

// ---- toasts ----------------------------------------------------------------------------------------------------
type Toast = { id: number; text: string; tone: 'ok' | 'bad' };
const ToastCtx = createContext<(text: string, tone?: 'ok' | 'bad') => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: 'ok' | 'bad' = 'ok') => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-2), { id, text, tone }]);
  }, []);
  const drop = useCallback((id: number) => setToasts((xs) => xs.filter((x) => x.id !== id)), []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => <ToastItem key={t.id} toast={t} onDone={drop} />)}
      </div>
    </ToastCtx.Provider>
  );
}

/** Disappears by itself (errors after 12 s, others after 5 s) but never while the pointer or keyboard focus is on it, so it can be read in full. */
function ToastItem({ toast, onDone }: { toast: Toast; onDone: (id: number) => void }) {
  const [held, setHeld] = useState(false);
  useEffect(() => {
    if (held) return;
    const timer = setTimeout(() => onDone(toast.id), toast.tone === 'bad' ? 12000 : 5000);
    return () => clearTimeout(timer);
  }, [held, toast.id, toast.tone, onDone]);
  return (
    <div className={`toast ${toast.tone}`} role={toast.tone === 'bad' ? 'alert' : undefined}
      onMouseEnter={() => setHeld(true)} onMouseLeave={() => setHeld(false)} onFocus={() => setHeld(true)} onBlur={() => setHeld(false)}>
      <span>{toast.text}</span>
      <button type="button" className="toast-x" aria-label="Dismiss" onClick={() => onDone(toast.id)}>✕</button>
    </div>
  );
}

// ---- skeleton --------------------------------------------------------------------------------------------------
export function Skeleton({ h = 16, w = '100%', style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden />;
}

export function ChartSkeleton({ height = 240 }: { height?: number }) {
  return <div className="chart-skel" style={{ height }} aria-busy="true" aria-label="Loading chart"><Skeleton h={height - 24} /></div>;
}

// ---- popover that closes on outside click or Escape ----------------------------------------------------------------
export function Popover({ label, children, active, align = 'left', name }: { label: ReactNode; children: ReactNode; active?: boolean; align?: 'left' | 'right'; name?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const close = useCallback((restoreFocus: boolean) => { setOpen(false); if (restoreFocus) button.current?.focus(); }, []);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) close(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); close(true); } };
    document.addEventListener('mousedown', down);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key); };
  }, [open, close]);
  // Tab out of the panel (forwards past the last control) closes it, as with any menu.
  const leave = (e: React.FocusEvent) => { if (open && ref.current && !ref.current.contains(e.relatedTarget as Node | null)) setOpen(false); };
  return (
    <div className="popover" ref={ref} onBlur={leave}>
      <button ref={button} type="button" className={`btn pop-btn${active ? ' active' : ''}`} aria-expanded={open} aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)} onKeyDown={(e) => { if (e.key === 'ArrowDown' && !open) { e.preventDefault(); setOpen(true); } }}>{label}<span aria-hidden>▾</span></button>
      {open && <div id={panelId} role="group" aria-label={name} className={`pop-panel ${align}`}>{children}</div>}
    </div>
  );
}

// ---- multi-select with search ----------------------------------------------------------------------------------------
export function MultiSelect({ title, options, selected, onChange, allowNone = true }: {
  title: string; options: { value: string; label?: string; count?: number }[]; selected: string[]; onChange: (v: string[]) => void; allowNone?: boolean;
}) {
  const [text, setText] = useState('');
  const shown = options.filter((o) => (o.label ?? o.value).toLowerCase().includes(text.toLowerCase()));
  const toggle = (v: string) => onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  return (
    <Popover name={title} label={<>{title}{selected.length > 0 && <span className="count-pill">{selected.length}</span>}</>} active={selected.length > 0}>
      {options.length > 8 && <input type="text" placeholder={`Search ${title.toLowerCase()}`} value={text} onChange={(e) => setText(e.target.value)} aria-label={`Search ${title}`} autoFocus />}
      <div className="opt-list" role="group" aria-label={title}>
        {allowNone && !text && (
          <label className="check"><input type="checkbox" checked={selected.includes('__none__')} onChange={() => toggle('__none__')} /><span className="muted">Not stated</span></label>
        )}
        {shown.map((o) => (
          <label key={o.value} className="check">
            <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
            <span className="opt-name">{o.label ?? o.value}</span>{o.count !== undefined && <span className="muted">{o.count}</span>}
          </label>
        ))}
        {shown.length === 0 && <div className="muted" style={{ padding: 8 }}>No matches</div>}
      </div>
      {selected.length > 0 && <button type="button" className="link-btn" onClick={() => onChange([])}>Clear {title.toLowerCase()}</button>}
    </Popover>
  );
}

// ---- slide-over panel --------------------------------------------------------------------------------------------
export function Drawer({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden'; // the page behind does not scroll while the panel is open
    ref.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key !== 'Tab' || !ref.current) return;
      // keep Tab inside the panel while it is open
      const f = Array.from(ref.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); document.body.style.overflow = overflow; prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="drawer-wrap">
      <div className="drawer-back" onClick={onClose} />
      <div className={`drawer${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <div className="drawer-head"><h2>{title}</h2><button className="link-btn" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="drawer-body">{children}</div>
      </div>
    </div>
  );
}
