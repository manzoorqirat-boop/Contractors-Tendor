import { useEffect, useRef, useState } from 'react';
import type { Tab } from './App';
import { tr } from './i18n';

const GO: Record<string, [Tab, string]> = { d: ['dashboard', tr('Dashboard')], e: ['explore', tr('Explore')], t: ['tenders', tr('Tenders')], b: ['bids', tr('Bids')], v: ['vault', tr('Vault')], r: ['runs', tr('Runs')], s: ['settings', tr('Settings')] };

const typing = (el: EventTarget | null) => {
  const t = el as HTMLElement | null;
  return !!t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable);
};

/** "?" opens the list; "g" then a letter jumps to a page. Ignored while typing, with a modifier key held, or with a dialog open. */
export function useShortcuts(go: (t: Tab) => void, allowed: (t: Tab) => boolean): [boolean, (open: boolean) => void] {
  const [help, setHelp] = useState(false);
  const pending = useRef(0);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || document.querySelector('[role=dialog], [role=alertdialog]')) return;
      if (e.key === '?') { e.preventDefault(); setHelp(true); return; }
      if (e.key === 'g') { pending.current = Date.now(); return; }
      if (pending.current && Date.now() - pending.current < 1500 && GO[e.key]) {
        pending.current = 0;
        if (allowed(GO[e.key][0])) { e.preventDefault(); go(GO[e.key][0]); }
      }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [go, allowed]);
  return [help, setHelp];
}

export function ShortcutsHelp({ onClose, allowed }: { onClose: () => void; allowed: (t: Tab) => boolean }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('keydown', esc); before?.focus?.(); };
  }, [onClose]);
  const rows: [string, string][] = [
    ['/', tr('Search the tender list')], ['?', tr('Show this list')], ['Esc', tr('Close a panel, menu or dialog')],
    ...Object.entries(GO).filter(([, [t]]) => allowed(t)).map(([k, [, label]]): [string, string] => [`g then ${k}`, `Go to ${label}`]),
  ];
  return (
    <div className="confirm-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="confirm-box" role="dialog" aria-modal="true" aria-labelledby="sc-title">
        <h2 id="sc-title">{tr('Keyboard shortcuts')}</h2>
        <dl className="shortcuts">{rows.map(([k, what]) => <div key={k}><dt>{k.split(' then ').map((p, i) => <span key={i}>{i > 0 && ' then '}<kbd>{p}</kbd></span>)}</dt><dd>{what}</dd></div>)}</dl>
        <p className="muted small">{tr('Shortcuts pause while you are typing in a box.')}</p>
        <div className="confirm-actions"><button type="button" className="btn primary" ref={close} onClick={onClose}>{tr('Close')}</button></div>
      </div>
    </div>
  );
}
