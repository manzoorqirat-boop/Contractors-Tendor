import { useEffect, useState } from 'react';
import { api } from './api';
import { readBase64, useAiStatus } from './ai';
import { fmtDate, inr, parseMoney } from './format';
import { Drawer, Skeleton, useToast } from './ui';
import { useConfirm } from './confirm';

interface Form { tender_id: string; title: string; description: string; organisation: string; location: string; value: string; emd: string; published: string; closing: string; opening: string; portal_name: string; reference: string }
const BLANK: Form = { tender_id: '', title: '', description: '', organisation: '', location: '', value: '', emd: '', published: '', closing: '', opening: '', portal_name: '', reference: '' };
type Step = 'start' | 'form' | 'done';

/** The panel's value for a datetime-local box. A date with no time gets 17:00 (the usual closing hour) and is flagged for checking. */
const dt = (v: string | null | undefined): [string, boolean] => (!v ? ['', false] : v.length <= 10 ? [`${v}T17:00`, true] : [v.slice(0, 16), false]);
/** The same rules the server applies, so a mistake is caught before it is sent. The server still has the last word. */
function validate(f: Form, editing: boolean): Record<string, string> {
  const e: Record<string, string> = {};
  const id = f.tender_id.trim();
  if (!editing && !id) e.tender_id = 'The Tender ID is how you find this tender on its portal. Copy it from the notice.';
  else if (/[<>\u0000-\u001f]/.test(id)) e.tender_id = 'The Tender ID cannot contain < or >. Copy it exactly as the portal shows it.';
  if (!f.description.trim() && !f.title.trim()) e.description = 'Say what the work is: a description, or at least a short name.';
  const value = money(f.value), emd = money(f.emd);
  if (f.value.trim() && (value === null || value <= 0)) e.value = 'Enter an amount like 12500000, 125 lakh or 1.25 crore.';
  if (f.emd.trim() && (emd === null || emd <= 0)) e.emd = 'Enter an amount like 250000 or 2.5 lakh.';
  if (!e.value && !e.emd && value !== null && emd !== null && emd > value) e.emd = 'The EMD is larger than the tender value. Check both amounts.';
  if (!f.closing) e.closing = 'The closing date is needed, so deadlines and reminders work.';
  else if (!editing && f.closing.slice(0, 10) < new Date().toISOString().slice(0, 10)) e.closing = 'This date has already passed, so bids can no longer be filed. Check the date.';
  if (f.opening && f.closing && f.opening < f.closing) e.opening = 'Bids cannot open before the tender closes.';
  if (f.published && f.closing && f.published > f.closing) e.published = 'The publish date is after the closing date.';
  return e;
}

const money = parseMoney;

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
  const confirm = useConfirm();
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
  const [tried, setTried] = useState(false);       // Save was pressed: show every problem, not just those of fields already visited
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const errs = validate(form, !!editKey);
  const err = (k: string) => (tried || touched[k] ? errs[k] ?? '' : '');
  const touch = (k: string) => setTouched((t) => (t[k] ? t : { ...t, [k]: true }));
  /** Props that tie an input to its message for screen readers. */
  const a11y = (k: string) => ({ 'aria-invalid': !!err(k) || undefined, 'aria-describedby': err(k) ? `err-${k}` : undefined, onBlur: () => touch(k) });
  const Msg = ({ k }: { k: string }) => (err(k) ? <span className="field-err" id={`err-${k}`}>{err(k)}</span> : null);
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
    setTried(true);
    const first = Object.keys(errs)[0];
    if (first) {
      setError('');
      document.querySelector<HTMLElement>(`[data-field="${first}"]`)?.focus();
      return;
    }
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
    if (!editKey || !(await confirm({ title: 'Remove this tender?', body: 'Its saved notice is deleted too, and this cannot be undone. A tender with a bid cannot be removed until the bid is.', confirmLabel: 'Remove tender', danger: true }))) return;
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
          <label className="field">Tender ID <span className="req">(required)</span><input data-field="tender_id" value={form.tender_id} onChange={(e) => set({ tender_id: e.target.value })} {...a11y('tender_id')} onBlur={(e) => { touch('tender_id'); checkId(e.target.value); }} maxLength={80} disabled={!!editKey} placeholder="As written on the notice" /><Msg k="tender_id" /></label>
          <label className="field">Work description <span className="req">(required, or a short name below)</span><textarea data-field="description" value={form.description} onChange={(e) => set({ description: e.target.value })} {...a11y('description')} maxLength={500} rows={3} /><Msg k="description" /></label>
          <label className="field">Short name (optional)<input value={form.title} onChange={(e) => set({ title: e.target.value })} maxLength={160} /></label>
          <label className="field">Department / office<input value={form.organisation} onChange={(e) => set({ organisation: e.target.value })} maxLength={300} /></label>
          <label className="field">Place of work<input value={form.location} onChange={(e) => set({ location: e.target.value })} maxLength={120} /></label>
          <div className="ws-grid">
            <label className="field-inline">Estimated value (₹){form.value && money(form.value) ? <span className="muted small"> {inr(money(form.value))}</span> : null}<input data-field="value" inputMode="decimal" value={form.value} onChange={(e) => set({ value: e.target.value })} {...a11y('value')} /><Msg k="value" /></label>
            <label className="field-inline">EMD (₹)<input data-field="emd" inputMode="decimal" value={form.emd} onChange={(e) => set({ emd: e.target.value })} {...a11y('emd')} /><Msg k="emd" /></label>
          </div>
          <div className="ws-grid" style={{ marginTop: 10 }}>
            <label className="field-inline">Bid closes <span className="req">(required)</span><input data-field="closing" type="datetime-local" value={form.closing} onChange={(e) => { set({ closing: e.target.value }); setAssumed((a) => ({ ...a, closing: false })); }} {...a11y('closing')} />{form.closing && <span className="date-say" aria-hidden>{fmtDate(form.closing, true)}</span>}<Msg k="closing" /></label>
            <label className="field-inline">Bids open<input data-field="opening" type="datetime-local" value={form.opening} onChange={(e) => { set({ opening: e.target.value }); setAssumed((a) => ({ ...a, opening: false })); }} {...a11y('opening')} />{form.opening && <span className="date-say" aria-hidden>{fmtDate(form.opening, true)}</span>}<Msg k="opening" /></label>
            <label className="field-inline">Published<input data-field="published" type="datetime-local" value={form.published} onChange={(e) => set({ published: e.target.value })} {...a11y('published')} />{form.published && <span className="date-say" aria-hidden>{fmtDate(form.published, true)}</span>}<Msg k="published" /></label>
          </div>
          {(assumed.closing || assumed.opening) && <p className="muted small" style={{ marginTop: 8 }}>The notice gave a date without a time, so 17:00 is filled in. Please set the real time.</p>}
          {!editKey && hasNotice && aiOn && <label className="check" style={{ marginTop: 12 }}><input type="checkbox" checked={readDoc} onChange={(e) => setReadDoc(e.target.checked)} /><span>Also read the notice for requirements and documents to submit</span></label>}
          {!editKey && !hasNotice && <p className="muted small" style={{ marginTop: 10 }}>No notice attached, so the checklist will have the standard steps only. You can upload the notice later from the bid.</p>}
          {error && <div className="alert bad" style={{ marginTop: 10 }}>{error}</div>}
          <div className="d-actions">
            <button className="btn primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save tender'}</button>
            {tried && Object.keys(errs).length > 0 && <span className="field-err" role="alert">{Object.keys(errs).length === 1 ? 'One thing needs fixing (shown above).' : `${Object.keys(errs).length} things need fixing (shown above).`}</span>}
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
