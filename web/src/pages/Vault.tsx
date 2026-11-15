import { useEffect, useState } from 'react';
import { api } from '../api';
import { readBase64 } from '../ai';
import { CataloguePanel } from '../Boq';
import { fmtDate } from '../format';
import { useLoad } from '../hooks';
import { Skeleton, useToast } from '../ui';
import type { AppConfig, CompanyFact, VaultDoc } from '../types';

const SUGGESTED_FACTS = ['Registration class', 'Turnover, last financial year', 'Turnover, year before', 'Turnover, two years before', 'Net worth', 'Largest similar work completed', 'Years in business', 'Bid capacity'];
const daysTo = (iso: string | null, now: string) => (iso ? Math.round((Date.parse(iso.slice(0, 10)) - Date.parse(now.slice(0, 10))) / 86_400_000) : null);
const size = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

export function VaultPage({ config }: { config: AppConfig }) {
  const toast = useToast();
  const { data, error, loading, reload } = useLoad(() => api.vault(), []);

  if (error) return <div className="alert bad">{error} <button className="link-btn" onClick={reload}>Try again</button></div>;
  if (loading && !data) return <Skeleton h={160} />;
  if (!data) return null;
  const expired = data.documents.filter((d) => { const l = daysTo(d.expiry, config.now); return l !== null && l < 0; }).length;
  const expiring = data.documents.filter((d) => { const l = daysTo(d.expiry, config.now); return l !== null && l >= 0 && l <= 30; }).length;

  return (
    <>
      <div className="page-head"><div><h1>Vault</h1><p className="sub">Papers you reuse in every bid, and the facts about your company. Bids match their required documents to these and warn when one expires before closing.</p></div></div>
      {(expired > 0 || expiring > 0) && (
        <div className={`vault-alert ${expired > 0 ? 'bad' : 'warn'}`} role="status">
          <strong>{expired > 0 ? `${expired} document${expired === 1 ? ' has' : 's have'} expired` : `${expiring} document${expiring === 1 ? ' expires' : 's expire'} within 30 days`}</strong>
          <span>{expired > 0 && expiring > 0 ? ` and ${expiring} more expire within 30 days. ` : ' '}Renew before you submit, because bids flag papers that expire before closing.</span>
        </div>
      )}
      <AddDocument kinds={data.kinds} onAdded={() => { reload(); toast('Added to the vault'); }} />
      <section className="card" style={{ marginBottom: 14 }}>
        <h2>Documents <span className="chip-n">{data.documents.length}</span></h2>
        {data.documents.length === 0 && <div className="empty"><h2>Your vault is empty</h2><p className="sub">Add your registration, GST, PAN, balance sheets, past work orders and affidavits. Bids will match their required documents to these.</p></div>}
        {data.documents.length > 0 && (
          <div className="scroll-x">
            <table className="tbl">
              <thead><tr><th>Document</th><th>Kind</th><th>Expires</th><th /></tr></thead>
              <tbody>
                {data.documents.map((d) => <DocRow key={d.id} d={d} kinds={data.kinds} now={config.now} onChange={reload} />)}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <CompanyProfile />
      <Facts facts={data.facts} />
      <CatalogueCard />
    </>
  );
}

function CompanyProfile() {
  const toast = useToast();
  const { data, error } = useLoad(() => api.settings(), []);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setText(data.company_profile || ''); }, [data]);
  const dirty = !!data && text.trim() !== (data.company_profile || '').trim();
  async function save() {
    setBusy(true);
    try { await api.saveSettings({ company_profile: text }); toast('Company profile saved. Open tenders are re-scored on the next run'); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  if (error) return <div className="alert bad">{error}</div>;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>About your company</h2>
      <p className="sub">Used to score how well each tender fits you. Say what work you do, where, the sizes you can take and your registration class.</p>
      <label className="field"><span className="sr-only">About your company</span>
        <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} disabled={!data} placeholder="Class A civil contractor. Roads and drainage in Pune and Nashik. Projects from 50 lakh to 10 crore. 8 years of PWD work." />
      </label>
      <button className="btn primary" onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save profile'}</button>
    </section>
  );
}

function CatalogueCard() {
  const cat = useLoad(() => api.boqCatalogue(), []);
  const count = cat.data?.counts?.item_master ?? 0;
  return (
    <section className="card" style={{ marginTop: 14 }}>
      <h2>Item catalogue ({count})</h2>
      <p className="sub">Your items and rates, loaded once from your ERP. Every bid's BOQ is matched against it. Loading the same code again updates it.</p>
      <details open={count === 0}><summary className="muted small">{count === 0 ? 'Load your catalogue' : 'Load or update items, rates, schedule of rates, location index'}</summary>
        <div style={{ marginTop: 10 }}><CataloguePanel onDone={cat.reload} /></div>
      </details>
    </section>
  );
}

function DocRow({ d, kinds, now, onChange }: { d: VaultDoc; kinds: { key: string; label: string }[]; now: string; onChange: () => void }) {
  const toast = useToast();
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ name: d.name, kind: d.kind, expiry: d.expiry ?? '', notes: d.notes });
  useEffect(() => setForm({ name: d.name, kind: d.kind, expiry: d.expiry ?? '', notes: d.notes }), [d]);
  const left = daysTo(d.expiry, now);
  const label = kinds.find((k) => k.key === d.kind)?.label ?? d.kind;

  async function save() {
    try { await api.patchVault(d.id, { name: form.name, kind: form.kind, expiry: form.expiry, notes: form.notes }); setEdit(false); onChange(); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function remove() {
    if (!window.confirm(`Remove "${d.name}" from the vault?${d.filename ? ' The stored file is deleted too.' : ''}`)) return;
    try { await api.deleteVault(d.id); onChange(); } catch (e) { toast((e as Error).message, 'bad'); }
  }

  if (edit) {
    return (
      <tr><td colSpan={4}>
        <div className="ws-grid">
          <label className="field-inline">Name<input value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field-inline">Kind<select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}</select></label>
          <label className="field-inline">Expires<input type="date" value={form.expiry} onChange={(e) => setForm({ ...form, expiry: e.target.value })} /></label>
          <label className="field-inline">Notes<input value={form.notes} maxLength={500} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
        </div>
        <div className="d-actions"><button className="btn primary small" onClick={save} disabled={!form.name.trim()}>Save</button><button className="btn small" onClick={() => setEdit(false)}>Cancel</button></div>
      </td></tr>
    );
  }
  return (
    <tr className={left !== null && left < 0 ? 'row-bad' : left !== null && left <= 30 ? 'row-warn' : ''}>
      <td><strong>{d.name}</strong>{d.notes && <div className="muted small">{d.notes}</div>}{d.filename && <div className="muted small">{d.filename} · {size(d.size)}</div>}</td>
      <td>{label}</td>
      <td>{d.expiry ? <>{fmtDate(d.expiry)} <span className={`badge ${left !== null && left < 0 ? 'bad' : left !== null && left <= 30 ? 'warn' : 'plain'}`}>{left !== null && left < 0 ? 'Expired' : left !== null && left <= 30 ? `${left} day${left === 1 ? '' : 's'} left` : 'Valid'}</span></> : <span className="muted">No expiry</span>}</td>
      <td className="num">
        {d.filename && <a className="btn small" href={api.vaultFileUrl(d.id)} download>Download</a>}{' '}
        <button className="btn small" onClick={() => setEdit(true)}>Edit</button>{' '}
        <button className="link-btn" aria-label={`Remove ${d.name}`} onClick={remove}>✕</button>
      </td>
    </tr>
  );
}

function AddDocument({ kinds, onAdded }: { kinds: { key: string; label: string }[]; onAdded: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: '', kind: 'registration', issued: '', expiry: '', notes: '' });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.addVault({ ...form, filename: file?.name ?? '', file_base64: file ? await readBase64(file) : '' });
      setForm({ name: '', kind: form.kind, issued: '', expiry: '', notes: '' }); setFile(null); setOpen(false); onAdded();
    } catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(false); }
  }
  if (!open) return <div style={{ margin: '0 0 14px' }}><button className="btn primary" onClick={() => setOpen(true)}>Add a document</button></div>;
  return (
    <form className="card" style={{ marginBottom: 14 }} onSubmit={add}>
      <h2>Add a document</h2>
      <div className="ws-grid" style={{ marginTop: 10 }}>
        <label className="field-inline">Name<input value={form.name} maxLength={120} required placeholder="e.g. GST certificate" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label className="field-inline">Kind<select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}</select></label>
        <label className="field-inline">Issued<input type="date" value={form.issued} onChange={(e) => setForm({ ...form, issued: e.target.value })} /></label>
        <label className="field-inline">Expires<input type="date" value={form.expiry} onChange={(e) => setForm({ ...form, expiry: e.target.value })} /></label>
      </div>
      <label className="field" style={{ marginTop: 10 }}>Notes<input value={form.notes} maxLength={500} placeholder="Optional: where the original is kept, certificate number…" onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
      <label className="field">File (optional, up to 10 MB: PDF, image, Word, Excel, zip)<input type="file" accept=".pdf,.png,.jpg,.jpeg,.doc,.docx,.xls,.xlsx,.txt,.zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
      <div className="d-actions"><button className="btn primary" disabled={busy || !form.name.trim()}>{busy ? 'Saving…' : 'Save'}</button><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button></div>
    </form>
  );
}

function Facts({ facts }: { facts: CompanyFact[] }) {
  const toast = useToast();
  const [rows, setRows] = useState<CompanyFact[]>(facts);
  const [busy, setBusy] = useState(false);
  useEffect(() => setRows(facts), [facts]);
  const dirty = JSON.stringify(rows.filter((r) => r.label.trim() && r.value.trim())) !== JSON.stringify(facts);
  const set = (i: number, patch: Partial<CompanyFact>) => setRows((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const missing = SUGGESTED_FACTS.filter((s) => !rows.some((r) => r.label.toLowerCase() === s.toLowerCase()));

  async function save() {
    setBusy(true);
    try { const r = await api.saveFacts(rows); setRows(r.facts); toast('Company facts saved'); } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Company facts</h2>
      <p className="sub">Used by the eligibility check and the draft letters. Only what you type here is used; nothing is guessed.</p>
      {rows.map((r, i) => (
        <div className="fact-row" key={i}>
          <input value={r.label} maxLength={60} placeholder="What" aria-label="Fact name" onChange={(e) => set(i, { label: e.target.value })} />
          <input value={r.value} maxLength={160} placeholder="Value" aria-label="Fact value" onChange={(e) => set(i, { value: e.target.value })} />
          <button type="button" className="link-btn" aria-label="Remove fact" onClick={() => setRows((xs) => xs.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <div className="chips" style={{ margin: '8px 0' }}>
        {missing.slice(0, 5).map((s) => <button key={s} type="button" className="chip" onClick={() => setRows((xs) => [...xs, { label: s, value: '' }])}>+ {s}</button>)}
        <button type="button" className="chip" onClick={() => setRows((xs) => [...xs, { label: '', value: '' }])}>+ Other</button>
      </div>
      <button className="btn primary" onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save facts'}</button>
    </section>
  );
}
