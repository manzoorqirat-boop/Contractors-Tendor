import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { describe, fromParams, toParams, VALUE_PRESETS, EMPTY } from './filters';
import { useDebounced } from './hooks';
import { MultiSelect, Popover, useToast } from './ui';
import type { AppConfig, Display, Facets, Filters, SavedView } from './types';

interface Props {
  filters: Filters;
  display: Display;
  config: AppConfig;
  facets: Facets | null;
  onChange: (f: Filters) => void;
  onApplyView: (f: Filters, d: Partial<Display>) => void;
  hideStatus?: boolean;
}

const SAVED_KEYS: (keyof Display)[] = ['sort', 'group_by', 'split_by', 'measure', 'chart'];

export function FilterBar({ filters, display, config, facets, onChange, onApplyView, hideStatus }: Props) {
  const toast = useToast();
  const [q, setQ] = useState(filters.q);
  const debounced = useDebounced(q, 350);
  const input = useRef<HTMLInputElement>(null);
  const [more, setMore] = useState(false); // phones start with the extra filters folded away
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });

  useEffect(() => { setQ(filters.q); }, [filters.q]);
  useEffect(() => { if (debounced !== filters.q) onChange({ ...filters, q: debounced }); /* eslint-disable-next-line */ }, [debounced]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) { e.preventDefault(); input.current?.focus(); }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, []);

  const chips = describe(filters, config);
  const preset = VALUE_PRESETS.findIndex((p) => (p.min ?? '') === (filters.min_value ?? '') && (p.max ?? '') === (filters.max_value ?? '') && !filters.value_lt && !filters.no_value);

  async function copyLink() {
    try { await navigator.clipboard.writeText(location.href); toast('Link to this view copied'); }
    catch { toast('Could not copy. The address bar has the link.', 'bad'); }
  }

  return (
    <section className="card filterbar" aria-label="Filters">
      <div className="fb-row">
        <input ref={input} type="text" className="fb-search" placeholder="Search work, department, location or Tender ID   ( / )" aria-label="Search" value={q} onChange={(e) => setQ(e.target.value)} maxLength={100} />
        <button type="button" className={`btn fb-toggle${more ? ' active' : ''}`} aria-expanded={more} onClick={() => setMore((m) => !m)}>Filters{chips.length ? ` (${chips.length})` : ''}</button>
        <div className={`fb-selects${more ? ' open' : ''}`}>
        {!hideStatus && (
          <select aria-label="Status" value={filters.status} onChange={(e) => set({ status: e.target.value as Filters['status'], closing_within: undefined })} className="fb-select">
            <option value="open">Open</option><option value="closing_soon">Closing in {config.soon_days} days</option><option value="closed">Closed</option><option value="all">Open and closed</option>
          </select>
        )}
        <select aria-label="Closing within" value={filters.closing_within ?? ''} onChange={(e) => set({ closing_within: e.target.value || undefined, status: e.target.value ? 'open' : filters.status })} className="fb-select">
          <option value="">Any closing date</option><option value="3">Closes within 3 days</option><option value="7">Within 7 days</option><option value="14">Within 14 days</option><option value="30">Within 30 days</option>
        </select>
        </div>
      </div>

      <div className={`fb-row fb-more${more ? ' open' : ''}`}>
        <div className="seg" role="group" aria-label="Portal">
          {config.portals.map((p) => {
            const on = filters.portal.includes(p.key);
            return <button key={p.key} type="button" aria-pressed={on} className={on ? 'on' : ''} onClick={() => set({ portal: on ? filters.portal.filter((x) => x !== p.key) : [...filters.portal, p.key] })}>{p.state}</button>;
          })}
        </div>
        {(facets?.work_type?.length ?? 0) > 0 && <MultiSelect title="Work type" options={facets?.work_type ?? []} selected={filters.work_type} onChange={(v) => set({ work_type: v })} allowNone={false} />}
        <MultiSelect title="Category" options={facets?.category ?? []} selected={filters.category} onChange={(v) => set({ category: v })} />
        <MultiSelect title="Department" options={facets?.department ?? []} selected={filters.department} onChange={(v) => set({ department: v })} />
        <MultiSelect title="Location" options={facets?.location ?? []} selected={filters.location} onChange={(v) => set({ location: v })} />
        <Popover name="Value" label="Value" active={chips.some((c) => c.id === 'value' || c.id === 'no_value')}>
          <div className="opt-list" role="radiogroup" aria-label="Estimated value">
            {VALUE_PRESETS.map((p, i) => (
              <label key={p.label} className="check"><input type="radio" name="vp" checked={preset === i} onChange={() => set({ min_value: p.min, max_value: p.max, value_lt: undefined, no_value: false })} />{p.label}</label>
            ))}
            <label className="check"><input type="radio" name="vp" checked={!!filters.no_value} onChange={() => set({ no_value: true, min_value: undefined, max_value: undefined, value_lt: undefined })} />Value not stated</label>
          </div>
          <div className="range-inputs">
            <input type="number" min={0} placeholder="Min ₹" aria-label="Minimum value in rupees" value={filters.min_value ?? ''} onChange={(e) => set({ min_value: e.target.value || undefined, no_value: false })} />
            <input type="number" min={0} placeholder="Max ₹" aria-label="Maximum value in rupees" value={filters.max_value ?? ''} onChange={(e) => set({ max_value: e.target.value || undefined, value_lt: undefined, no_value: false })} />
          </div>
        </Popover>
        <button type="button" className={`btn${filters.starred ? ' active' : ''}`} aria-pressed={!!filters.starred} onClick={() => set({ starred: !filters.starred })}>{filters.starred ? '★' : '☆'} Starred</button>
        <span className="spacer" />
        <ViewsMenu filters={filters} display={display} onApply={onApplyView} />
        <button type="button" className="btn" onClick={copyLink} title="Copy a link to exactly this view">Share</button>
      </div>

      {chips.length > 0 && (
        <div className="chips-row" aria-label="Active filters">
          {chips.map((c) => (
            <button key={c.id} type="button" className="chip" onClick={() => onChange(c.remove(filters))} aria-label={`Remove filter: ${c.label}`}>{c.label} <span aria-hidden>✕</span></button>
          ))}
          <button type="button" className="link-btn" onClick={() => onChange({ ...EMPTY, status: filters.status === 'open' ? 'open' : EMPTY.status })}>Clear all</button>
        </div>
      )}
    </section>
  );
}

function ViewsMenu({ filters, display, onApply }: { filters: Filters; display: Display; onApply: (f: Filters, d: Partial<Display>) => void }) {
  const toast = useToast();
  const [views, setViews] = useState<SavedView[] | null>(null);
  const [name, setName] = useState('');
  const [alertOn, setAlertOn] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.views().then((r) => setViews(r.views), () => setViews([])); }, []);

  async function persist(next: SavedView[], message: string) {
    setBusy(true);
    try { const r = await api.saveViews(next); setViews(r.views); toast(message); }
    catch (e) { toast((e as Error).message, 'bad'); }
    finally { setBusy(false); }
  }

  function save() {
    const trimmed = name.trim();
    if (!trimmed) return;
    const params = toParams(filters, Object.fromEntries(SAVED_KEYS.map((k) => [k, display[k]])) as Partial<Display>);
    const obj: Record<string, unknown> = {};
    for (const k of new Set(params.keys())) obj[k] = params.getAll(k).length > 1 || ['portal', 'category', 'department', 'location', 'work_type'].includes(k) ? params.getAll(k) : params.get(k);
    for (const k of ['no_value', 'no_closing', 'no_published', 'starred']) if (obj[k] === 'true') obj[k] = true;
    persist([...(views ?? []), { id: String(Date.now()), name: trimmed, filters: obj, alert: alertOn }], alertOn ? `Saved "${trimmed}". New matches will be in your daily email.` : `Saved view "${trimmed}"`);
    setName(''); setAlertOn(false);
  }

  function apply(v: SavedView) {
    const p = new URLSearchParams();
    for (const [k, val] of Object.entries(v.filters)) { if (Array.isArray(val)) val.forEach((x) => p.append(k, String(x))); else if (val !== undefined && val !== null) p.set(k, String(val)); }
    const { filters: f, display: d } = fromParams(p);
    onApply(f, { sort: d.sort, group_by: d.group_by, split_by: d.split_by, measure: d.measure, chart: d.chart, page: 1 });
  }

  return (
    <Popover name="Views" label={<>Views{views && views.length > 0 && <span className="count-pill">{views.length}</span>}</>} align="right">
      <div className="opt-list">
        {views === null && <div className="muted" style={{ padding: 8 }}>Loading…</div>}
        {views?.length === 0 && <div className="muted" style={{ padding: 8 }}>No saved views yet. Set some filters, name them below and save.</div>}
        {views?.map((v) => (
          <div key={v.id} className="view-row">
            <button type="button" className="link-btn view-name" onClick={() => apply(v)}>{v.name}</button>
            <label className="view-alert" title="Add the new tenders that match this view to your daily email"><input type="checkbox" checked={!!v.alert} disabled={busy} onChange={(e) => persist((views ?? []).map((x) => (x.id === v.id ? { ...x, alert: e.target.checked } : x)), e.target.checked ? `New matches of "${v.name}" will be in your daily email.` : 'Email alert turned off')} /> Email</label>
            <button type="button" className="link-btn" disabled={busy} aria-label={`Delete view ${v.name}`} onClick={() => persist((views ?? []).filter((x) => x.id !== v.id), 'View deleted')}>✕</button>
          </div>
        ))}
      </div>
      <form className="view-save" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <input type="text" placeholder="Name this view" aria-label="View name" maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn small primary" disabled={!name.trim() || busy}>Save</button>
        <label className="check small" style={{ flexBasis: '100%', margin: '6px 0 0' }}><input type="checkbox" checked={alertOn} onChange={(e) => setAlertOn(e.target.checked)} />Email me new matches in the daily digest</label>
      </form>
    </Popover>
  );
}
