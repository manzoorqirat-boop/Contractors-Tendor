import { useEffect, useRef, type ReactNode } from 'react';

/** For a row of tabs that scrolls sideways on a small screen: keeps the selected tab in view, and returns the ref to put on the row. */
export function useKeepActiveVisible(active: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ inline: 'nearest', block: 'nearest' }); }, [active]);
  return ref;
}

/** A row of tabs with arrow-key movement; the same look as the tabs on a bid. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { key: T; label: ReactNode }[]; value: T; onChange: (k: T) => void; label: string }) {
  const rowRef = useKeepActiveVisible(value);
  const move = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const i = tabs.findIndex((t) => t.key === value);
    onChange(tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length].key);
    e.preventDefault();
    const bar = e.currentTarget;
    setTimeout(() => (bar.querySelector('[aria-selected="true"]') as HTMLElement | null)?.focus(), 0);
  };
  return (
    <div className="ws-tabs" role="tablist" aria-label={label} onKeyDown={move} ref={rowRef}>
      {tabs.map((t) => (
        <button key={t.key} type="button" role="tab" aria-selected={value === t.key} tabIndex={value === t.key ? 0 : -1} className={value === t.key ? 'on' : ''} onClick={() => onChange(t.key)}>{t.label}</button>
      ))}
    </div>
  );
}
