import { useEffect, useState } from 'react';
import { api } from './api';
import { readBase64, useAiStatus } from './ai';
import { inr } from './format';
import { Drawer, Skeleton, useToast } from './ui';

interface Form { tender_id: string; title: string; description: string; organisation: string; location: string; value: string; emd: string; published: string; closing: string; opening: string; portal_name: string; reference: string }
const BLANK: Form = { tender_id: '', title: '', description: '', organisation: '', location: '', value: '', emd: '', published: '', closing: '', opening: '', portal_name: '', reference: '' };
type Step = 'start' | 'form' | 'done';

/** The panel's value for a datetime-local box. A date with no time gets 17:00 (the usual closing hour) and is flagged for checking. */
const dt = (v: string | null | undefined): [string, boolean] => (!v ? ['', false] : v.length <= 10 ? [`${v}T17:00`, true] : [v.slice(0, 16), false]);
const money = (v: string): number | null => { const n = Number(v.replace(/[,\s₹]/g, '')); return v.trim() === '' || Number.isNaN(n) ? null : n; };

/** Opens on the 'add-tender' event: detail {} adds a tender; detail {editKey} corrects one that was added by hand. */
export function AddTenderHost() {
  const [req, setReq] = useState<{ editKey?: string } | null>(null);
  useEffect(() => {
    const open = (e: Event) => setReq((e as CustomEvent<{ editKey?: string } | undefined>).detail ?? {});
    window.addEventListener('add-tender', open);
    return () => window.removeEventListener('add-tender', open);
  }, []);
  if (!req) return null;
  return <AddTender editKey={req.editKey} onClose={() => setReq(null)} />;
}

function AddTender({ editKey, onClose }: { editKey?: string; onClose: () => void }) {
  const toast = useToast();
  const ai = useAiStatus();
  const aiOn = !!ai?.configured;
  const [step, setStep] = useState<Step>(editKey ? 'form' : 'start');
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState('');
  const [form, setForm] = useState<Form>(BLANK);
  const [assumed, setAssumed] = useState<{ closing: boolean; opening: boolean }>({ closing: false, opening: false });
  const [readDoc, setReadDoc] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(!!editKey);
  const [error, setError] = useState('');
  const [existing, setExisting] = useState<{ key: string; portal_name: string; organisation: string; manual: boolean }[]>([]);
  const [result, setResult] = useState<{ key: string; doc_read: boolean; doc_error: string; source_kept: boolean } | null>(null);
  const hasNotice = !!file || !!paste.trim();
  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));

  useEffect(() => {
    if (!editKey) return;
    api.tender(editKey).then((t) => {
      const [closing] = dt(t.closing), [opening] = dt(t.opening), [published] = dt(t.published);
      setForm({ tender_id: t.tender_id, title: t.title, description: t.description, organisation: t.organisation ?? '', location: t.location ?? '', value: t.value ? String(t.value) : '', emd: t.emd ? String(t.emd) : '',
        published, closing, opening, portal_name: '', reference: '' });
      setLoading(false);
    }, (e: Error) => { setError(e.message); setLoading(false); });
  }, [editKey]);

  async function readNotice() {
    setBusy(true); setError('');
    try {
      const { listing: l } = await api.manualRead(file ? '' : paste, file ? await readBase64(file) : '');
      const [closing, ca] = dt(l.closing), [opening, oa] = dt(l.opening), [published] = dt(l.published);
      setForm({ tender_id: l.tender_id, title: l.title, description: l.description, organisation: l.organisation, location: l.location, value: l.value ? String(l.value) : '', emd: l.emd ? String(l.emd) : '',
        published, closing, opening, portal_name: l.portal_name, reference: l.reference });
      setAssumed({ closing: ca, opening: oa });
      setStep('form');
      if (l.tender_id) checkId(l.tender_id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function checkId(id: string) {
    if (editKey || !id.trim()) return;
    api.manualCheck(id.trim()).then((r) => setExisting(r.existing), () => undefined);
  }

  async function save() {
    setBusy(true); setError('');
    try {
      const common = { description: form.description, title: form.title, organisation: form.organisation, location: form.location, closing: form.closing, opening: form.opening, published: form.published };
      if (editKey) {
        await api.manualEdit(editKey, { ...common, tender_id: undefined, value: money(form.value), emd: money(form.emd), clear_value: !form.value.trim(), clear_emd: !form.emd.trim() });
        toast('Tender updated'); window.dispatchEvent(new Event('tenders-changed')); onClose();
        return;
      }
      const r = await api.manualCreate({ ...common, tender_id: form.tender_id, reference: form.reference, portal_name: form.portal_name, value: money(form.value), emd: money(form.emd),
        text: file ? '' : paste, pdf_base64: file ? await readBase64(file) : '', read_document: readDoc && aiOn });
      setResult(r); setStep('done'); window.dispatchEvent(new Event('tenders-changed'));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function remove() {
    if (!editKey || !window.confirm('Remove this tender? Its saved notice is deleted too. A tender with a bid cannot be removed until the bid is.')) return;
    try { await api.manualDelete(editKey); toast('Tender removed'); window.dispatchEvent(new Event('tenders-changed')); onClose(); } catch (e) { setError((e as Error).message); }
  }

  async function startBid(key: string) {
    try {
      const r = await api.createBid(key);
      onClose();
      window.dispatchEvent(new CustomEvent('open-bid', { detail: r.id }));
    } catch (e) { setError((e as Error).message); }
  }

  const title = editKey ? 'Edit this tender' : 'Add a tender by hand';
  return (
    <Drawer title={title} onClose={onClose}>
      {loading && <Skeleton h={160} />}

      {step === 'start' && (
        <>
          <p className="sub">For a tender the app cannot collect: a portal with a captcha or login, a newspaper notice, or an NIT sent to you. Give it the notice and it fills in the details for you to check.</p>
          <label className="field">Notice (PDF, up to 10 MB)<input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="field">Or paste the text of the notice<textarea value={paste} onChange={(e) => setPaste(e.target.value)} disabled={!!file} spellCheck={false} rows={6} /></label>
          {error && <div className="alert bad">{error}</div>}
          <div className="d-actions">
            {aiOn && <button className="btn primary" onClick={readNotice} disabled={busy || !hasNotice}>{busy ? 'Reading the notice…' : 'Read the notice'}</button>}
            <button className="btn" onClick={() => setStep('form')}>{aiOn ? 'Type the details myself' : 'Continue'}</button>
          </div>
          {!aiOn && <p className="muted small">The AI assistant is off, so the details are typed in. Turn it on to have them read from the notice.</p>}
        </>
      )}

      {step === 'form' && !loading && (
        <>
          {!editKey && hasNotice && <p className="sub">Check every detail against the notice, especially the closing date. Nothing is saved until you press Save.</p>}
          {existing.length > 0 && (
            <div className="alert warn">
              This Tender ID is already in the app ({existing.map((x) => x.manual ? 'added by hand' : x.portal_name).join(', ')}).
              {existing.map((x) => <div key={x.key}><button className="link-btn" onClick={() => startBid(x.key)}>Start a bid on that one</button></div>)}
            </div>
          )}
          <label className="field">Tender ID<input value={form.tender_id} onChange={(e) => set({ tender_id: e.target.value })} onBlur={(e) => checkId(e.target.value)} maxLength={80} disabled={!!editKey} placeholder="As written on the notice" /></label>
          <label className="field">Work description<textarea value={form.description} onChange={(e) => set({ description: e.target.value })} maxLength={500} rows={3} /></label>
          <label className="field">Short name (optional)<input value={form.title} onChange={(e) => set({ title: e.target.value })} maxLength={160} /></label>
          <label className="field">Department / office<input value={form.organisation} onChange={(e) => set({ organisation: e.target.value })} maxLength={300} /></label>
          <label className="field">Place of work<input value={form.location} onChange={(e) => set({ location: e.target.value })} maxLength={120} /></label>
          <div className="ws-grid">
            <label className="field-inline">Estimated value (₹){form.value && money(form.value) ? <span className="muted small"> {inr(money(form.value))}</span> : null}<input inputMode="decimal" value={form.value} onChange={(e) => set({ value: e.target.value })} /></label>
            <label className="field-inline">EMD (₹)<input inputMode="decimal" value={form.emd} onChange={(e) => set({ emd: e.target.value })} /></label>
          </div>
          <div className="ws-grid" style={{ marginTop: 10 }}>
            <label className="field-inline">Bid closes (needed)<input type="datetime-local" value={form.closing} onChange={(e) => { set({ closing: e.target.value }); setAssumed((a) => ({ ...a, closing: false })); }} /></label>
            <label className="field-inline">Bids open<input type="datetime-local" value={form.opening} onChange={(e) => { set({ opening: e.target.value }); setAssumed((a) => ({ ...a, opening: false })); }} /></label>
            <label className="field-inline">Published<input type="datetime-local" value={form.published} onChange={(e) => set({ published: e.target.value })} /></label>
          </div>
          {(assumed.closing || assumed.opening) && <p className="muted small" style={{ marginTop: 8 }}>The notice gave a date without a time, so 17:00 is filled in. Please set the real time.</p>}
          {!editKey && hasNotice && aiOn && <label className="check" style={{ marginTop: 12 }}><input type="checkbox" checked={readDoc} onChange={(e) => setReadDoc(e.target.checked)} /><span>Also read the notice for requirements and documents to submit</span></label>}
          {!editKey && !hasNotice && <p className="muted small" style={{ marginTop: 10 }}>No notice attached, so the checklist will have the standard steps only. You can upload the notice later from the bid.</p>}
          {error && <div className="alert bad" style={{ marginTop: 10 }}>{error}</div>}
          <div className="d-actions">
            <button className="btn primary" onClick={save} disabled={busy || !form.closing || (!editKey && !form.tender_id.trim()) || !(form.description.trim() || form.title.trim())}>{busy ? 'Saving…' : 'Save tender'}</button>
            {!editKey && <button className="btn" onClick={() => setStep('start')}>Back</button>}
            {editKey && <button className="link-btn" style={{ color: 'var(--bad)' }} onClick={remove}>Remove this tender</button>}
          </div>
        </>
      )}

      {step === 'done' && result && (
        <>
          <div className="alert ok">Saved. It is in your Tenders list as <strong>Added by hand</strong>.</div>
          <ul className="small" style={{ margin: '8px 0 12px 18px' }}>
            {result.source_kept && <li>The notice is kept with the tender, so the risk and BOQ steps need no second upload.</li>}
            {result.doc_read && <li>The notice was read: its requirements and documents are ready for the bid checklist.</li>}
            {result.doc_error && <li>{result.doc_error}</li>}
            <li>The app cannot see corrigenda on this tender. Check the portal yourself before closing; the bid will remind you.</li>
          </ul>
          <div className="d-actions">
            <button className="btn primary" onClick={() => startBid(result.key)}>Start a bid</button>
            <button className="btn" onClick={() => { setForm(BLANK); setFile(null); setPaste(''); setExisting([]); setResult(null); setStep('start'); }}>Add another</button>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </>
      )}
    </Drawer>
  );
}
