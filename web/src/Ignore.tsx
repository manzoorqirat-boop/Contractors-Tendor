import { useEffect, useState } from 'react';
import { api } from './api';
import { inr, parseMoney, plural } from './format';
import { useLoad } from './hooks';
import { Popover, Skeleton, useToast } from './ui';
import { useConfirm } from './confirm';
import { tr } from './i18n';
import type { CompanyFact, Learning, Tender } from './types';

const KIND: Record<string, string> = { department: tr('department'), category: tr('category'), 'type of work': tr('type of work'), wording: tr('wording'), place: tr('place'), portal: tr('portal') };
const deptOf = (org: string) => (org || '').split('||')[0].trim();

/** "Not interested" for the tenders ticked in the list: hide just those, or those and everything from their departments. */
export function NotInterestedMenu({ tenders, onDone }: { tenders: Tender[]; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const depts = [...new Set(tenders.map((t) => deptOf(t.organisation)).filter(Boolean))];
  async function hide(withDepartments: boolean) {
    setBusy(true);
    try {
      const r = await api.hideTenders(tenders.map((t) => t.key), true, withDepartments);
      toast(r.muted.length ? `Hid ${plural(r.changed, 'tender')} and everything from ${r.muted.join(', ')}. Bring them back in Vault > Company.` : `Hid ${plural(r.changed, 'tender')}. Choose "Show ignored" to bring them back.`);
      onDone();
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  return (
    <Popover label={tr('Not interested')} name={tr('Not interested')} align="right">
      <div style={{ display: 'grid', gap: 8, padding: 4 }}>
        <button type="button" className="btn small" disabled={busy} onClick={() => hide(false)}>{`Hide ${plural(tenders.length, 'tender')}`}</button>
        {depts.length > 0 && <button type="button" className="btn small" disabled={busy} onClick={() => hide(true)}>{depts.length === 1 ? `Hide these and everything from ${depts[0]}` : `Hide these and everything from their ${depts.length} departments`}</button>}
        <p className="muted small" style={{ margin: 0 }}>{tr('Hidden tenders leave the list, the counts and the daily email. You can bring them back.')}</p>
      </div>
    </Popover>
  );
}

/** Vault > Company: the smallest and largest tender this company takes, used by the "Fits my size" filter. */
export function CompanySize({ facts }: { facts: CompanyFact[] }) {
  const toast = useToast();
  const size = useLoad(() => api.companySize(), []);
  const [lo, setLo] = useState('');
  const [hi, setHi] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (size.data) { setLo(size.data.min ? String(size.data.min) : ''); setHi(size.data.max ? String(size.data.max) : ''); } }, [size.data]);
  const pLo = lo.trim() ? parseMoney(lo) : null, pHi = hi.trim() ? parseMoney(hi) : null;
  const bad = (lo.trim() !== '' && pLo === null) || (hi.trim() !== '' && pHi === null) || (pLo !== null && pHi !== null && pLo > pHi);
  const capacity = facts.find((f) => /bid capacity/i.test(f.label));
  const fromFact = capacity ? parseMoney(capacity.value.replace(/^rs\.?\s*/i, '')) : null;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try { await api.saveCompanySize(pLo, pHi); toast(pLo || pHi ? 'Saved. "Fits my size" in the tender list now uses this range.' : 'Saved. No size limit.'); size.reload(); }
    catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(false); }
  }
  return (
    <section className="card stack-fields">
      <h2>{tr('Size of work you take')}</h2>
      <p className="sub">{tr('The tender list has a "Fits my size" button that hides tenders outside this range. A tender with no stated value is kept, never hidden.')}</p>
      {size.loading && !size.data && <Skeleton h={60} />}
      <form onSubmit={save}>
        <label className="field">{tr('Smallest tender (₹)')}<span className="hint">e.g. 10 lakh. Leave empty for no minimum.</span><input value={lo} onChange={(e) => setLo(e.target.value)} inputMode="decimal" aria-invalid={bad || undefined} /></label>
        <label className="field">{tr('Largest tender (₹)')}<span className="hint">e.g. 5 crore. Often your bid capacity.</span><input value={hi} onChange={(e) => setHi(e.target.value)} inputMode="decimal" aria-invalid={bad || undefined} /></label>
        {bad && <p className="field-err">Use amounts like 1000000, 10 lakh or 1.5 crore, with the smallest not above the largest.</p>}
        {!bad && (pLo || pHi) && <p className="muted small">{pLo ? inr(pLo) : 'any size'} to {pHi ? inr(pHi) : 'any size'}</p>}
        <div className="d-actions">
          <button className="btn primary" disabled={busy || bad}>{busy ? tr('Saving…') : tr('Save')}</button>
          {fromFact && <button type="button" className="btn" onClick={() => setHi(String(fromFact))}>{`Use my bid capacity (${inr(fromFact)}) as the largest`}</button>}
        </div>
      </form>
    </section>
  );
}

/** Vault > Company: departments hidden with "Hide ... and everything from", each with a way back. */
export function HiddenDepartments() {
  const toast = useToast();
  const confirm = useConfirm();
  const mutes = useLoad(() => api.mutes(), []);
  const d = mutes.data;
  async function show(dept: string) {
    if (!(await confirm({ title: `Show ${dept} again?`, body: 'New and existing tenders from this department appear in the list again. Tenders you hid one by one stay hidden.', confirmLabel: 'Show again' }))) return;
    try { await api.setMute(dept, false); mutes.reload(); toast(`${dept} is shown again`); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  return (
    <section className="card">
      <h2>{tr('Hidden tenders and departments')}</h2>
      {mutes.loading && !d && <Skeleton h={50} />}
      {d && (
        <>
          <p className="sub">{d.hidden_tenders > 0 ? `${plural(d.hidden_tenders, 'tender')} hidden.` : 'Nothing is hidden.'} {d.hidden_tenders > 0 && <a href="#/tenders?hidden=only">{tr('Show ignored')}</a>}</p>
          {d.departments.length === 0 && <p className="muted small">{tr('No whole department is hidden. Use "Not interested" on selected tenders and choose to hide the department too.')}</p>}
          {d.departments.map((m) => (
            <div key={m.dept} style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid var(--border)', padding: '8px 0' }}>
              <span><strong>{m.dept}</strong><span className="muted small"> · {plural(m.tenders, 'tender')}</span></span>
              <button type="button" className="btn small" onClick={() => show(m.dept)}>{tr('Show again')}</button>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

/** Vault > Company: what the Recommended order has learned from stars, bids, hides and dropped bids, in plain words. */
export function LearningCard() {
  const learned = useLoad(() => api.learning(), []);
  const d = learned.data;
  const list = (items: Learning['liked'], empty: string) => items.length === 0
    ? <p className="muted small">{empty}</p>
    : <ul style={{ margin: 0, paddingLeft: 18 }}>{items.map((x) => <li key={x.kind + x.value}><strong>{x.value}</strong> <span className="muted small">({KIND[x.kind] ?? x.kind}, {Math.max(x.chosen, x.avoided)}×)</span></li>)}</ul>;
  return (
    <section className="card">
      <h2>{tr('What the Recommended order has learned')}</h2>
      {learned.loading && !d && <Skeleton h={50} />}
      {d && !d.enabled && <p className="sub">{tr('Learning is switched off (Settings, Civil filter tab). The Recommended order uses only closing time, fit and size.')}</p>}
      {d && d.enabled && !d.ready && <p className="sub">{`${plural(d.positive + d.negative, 'choice')} so far. Star or start a bid on tenders you want, and hide those you do not. After ${d.needed} choices the list starts to learn your taste.`}</p>}
      {d && d.enabled && d.ready && (
        <>
          <p className="sub">{`Learned from ${plural(d.positive, 'tender')} you starred or bid on and ${plural(d.negative, 'tender')} you hid or dropped. Each tender in the list says why it is placed where it is.`}</p>
          <div className="set-grid">
            <div><h3>{tr('You tend to choose')}</h3>{list(d.liked, 'Nothing stands out yet.')}</div>
            <div><h3>{tr('You tend to hide')}</h3>{list(d.avoided, 'Nothing stands out yet.')}</div>
          </div>
        </>
      )}
    </section>
  );
}
