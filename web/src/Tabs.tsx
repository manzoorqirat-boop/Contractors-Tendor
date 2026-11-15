import type { ReactNode } from 'react';

/** A row of tabs with arrow-key movement; the same look as the tabs on a bid. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { key: T; label: ReactNode }[]; value: T; onChange: (k: T) => void; label: string }) {
  const move = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const i = tabs.findIndex((t) => t.key === value);
    onChange(tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length].key);
    e.preventDefault();
    const bar = e.currentTarget;
    setTimeout(() => (bar.querySelector('[aria-selected="true"]') as HTMLElement | null)?.focus(), 0);
  };
  return (
    <div className="ws-tabs" role="tablist" aria-label={label} onKeyDown={move}>
      {tabs.map((t) => (
        <button key={t.key} type="button" role="tab" aria-selected={value === t.key} tabIndex={value === t.key ? 0 : -1} className={value === t.key ? 'on' : ''} onClick={() => onChange(t.key)}>{t.label}</button>
      ))}
    </div>
  );
}
