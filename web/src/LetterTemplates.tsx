import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { fmtDate } from './format';
import { useLoad } from './hooks';
import { Skeleton, useToast } from './ui';
import { useConfirm } from './confirm';
import { useSession } from './session';
import type { LetterTemplate, TemplateVersion } from './types';

const STARTER = `To,
The {{organisation}}

Subject: Bid for "{{title}}" (Tender ID {{tender_id}})

Dear Sir,

{{company_company_name}} submits its bid for the above work, which closes on {{closing}}.

Yours faithfully,
{{today}}`;

/** Company-wide letter wording with placeholders, kept with every earlier version so a change can be undone. Lives in the Vault. */
export function TemplateManager() {
  const toast = useToast();
  const confirm = useConfirm();
  const { can } = useSession();
  const data = useLoad(() => api.letterTemplates(), []);
  const [sel, setSel] = useState<number | 'new' | null>(null);
  const [name, setName] = useState('');
  const [body, setBody] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [versions, setVersions] = useState<TemplateVersion[] | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const list = data.data?.templates ?? [];
  const current = typeof sel === 'number' ? list.find((t) => t.id === sel) ?? null : null;
  const editable = can('bidder');

  useEffect(() => {
    if (sel === null && list.length) setSel(list[0].id);
  }, [list.length]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setVersions(null); setNote('');
    if (sel === 'new') { setName(''); setBody(STARTER); }
    else if (current) { setName(current.name); setBody(current.body); }
  }, [sel, current?.id, current?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  if (data.loading && !data.data) return <Skeleton h={160} />;
  if (data.error) return <div className="alert bad">{data.error} <button className="link-btn" onClick={data.reload}>Try again</button></div>;
  const dirty = sel === 'new' ? true : !!current && (current.body !== body || current.name !== name);

  function insert(key: string) {
    const el = box.current, token = `{{${key}}}`;
    if (!el) { setBody((b) => b + token); return; }
    const a = el.selectionStart, z = el.selectionEnd;
    setBody((b) => b.slice(0, a) + token + b.slice(z));
    setTimeout(() => { el.focus(); el.setSelectionRange(a + token.length, a + token.length); }, 0);
  }
  async function save() {
    setBusy(true);
    try {
      const t = sel === 'new' ? await api.createTemplate(name, body) : await api.saveTemplate(current!.id, name, body, note);
      toast(sel === 'new' ? 'Template added' : t.version === current!.version ? 'Nothing changed' : `Saved as version ${t.version}`);
      await data.reload();
      setSel(t.id);
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function showHistory() {
    if (!current) return;
    try { setVersions((await api.templateVersions(current.id)).versions); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function restore(v: number) {
    if (!current) return;
    if (!(await confirm({ title: `Put version ${v} back?`, body: 'It is saved as a new version, so nothing in the history is lost.', confirmLabel: 'Restore' }))) return;
    try { await api.restoreTemplate(current.id, v); toast(`Version ${v} is back`); await data.reload(); setVersions(null); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function remove() {
    if (!current) return;
    if (!(await confirm({ title: `Delete "${current.name}"?`, body: 'The template and all its earlier versions are removed. Letters already written are not affected.', confirmLabel: 'Delete', danger: true }))) return;
    try { await api.deleteTemplate(current.id); toast('Template deleted'); setSel(null); await data.reload(); } catch (e) { toast((e as Error).message, 'bad'); }
  }

  const fields = data.data?.fields ?? [];
  return (
    <section className="card stack-fields">
      <div className="card-head"><div><h2>Letter templates</h2><p className="sub">Wording you use again and again. Placeholders such as <code>{'{{tender_id}}'}</code> are filled from the bid and your company facts. Every save keeps the earlier wording.</p></div>
        {editable && <div className="head-actions"><button type="button" className="btn small" onClick={() => setSel('new')}>New template</button></div>}</div>
      {list.length === 0 && sel !== 'new' && <div className="empty"><h2>No templates yet</h2><p className="sub">Add a covering letter, an extension request or an undertaking once, then use it on any bid.</p></div>}
      {(list.length > 0 || sel === 'new') && (
        <div className="seg" role="group" aria-label="Templates" style={{ flexWrap: 'wrap', marginBottom: 10 }}>
          {list.map((t) => <button key={t.id} type="button" className={sel === t.id ? 'on' : ''} aria-pressed={sel === t.id} onClick={() => setSel(t.id)}>{t.name}</button>)}
          {sel === 'new' && <button type="button" className="on" aria-pressed>New template</button>}
        </div>
      )}
      {(current || sel === 'new') && (
        <>
          <label className="field">Name<input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="e.g. Covering letter" disabled={!editable} /></label>
          <label className="field">Wording<textarea ref={box} className="tpl-body" value={body} onChange={(e) => setBody(e.target.value)} spellCheck disabled={!editable} /></label>
          {editable && (
            <div className="chips" aria-label="Insert a placeholder">
              {fields.map((f) => <button key={f.key} type="button" className="chip-btn" title={`Insert {{${f.key}}}`} onClick={() => insert(f.key)}>{f.label}</button>)}
            </div>
          )}
          {editable && sel !== 'new' && <label className="field">What changed? (optional)<input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} /></label>}
          <div className="d-actions">
            {editable && <button type="button" className="btn primary" onClick={save} disabled={busy || !dirty || !name.trim() || !body.trim()}>{busy ? 'Saving…' : sel === 'new' ? 'Add template' : 'Save changes'}</button>}
            {current && <button type="button" className="btn" onClick={showHistory}>History (version {current.version})</button>}
            {current && editable && <button type="button" className="btn danger" onClick={remove}>Delete</button>}
          </div>
          {versions && (
            <ul className="plain" style={{ marginTop: 10 }}>
              {versions.map((v) => (
                <li key={v.version} style={{ padding: '6px 0', borderTop: '1px solid var(--border)' }}>
                  <strong>Version {v.version}</strong> <span className="muted small">{fmtDate(v.created)}{v.author ? `, ${v.author}` : ''}{v.note ? `: ${v.note}` : ''}</span>
                  {v.version === current?.version ? <span className="badge plain" style={{ marginLeft: 8 }}>current</span>
                    : editable && <button type="button" className="link-btn" style={{ marginLeft: 8 }} onClick={() => restore(v.version)}>Restore</button>}
                  <details><summary className="muted small">Show wording</summary><pre style={{ whiteSpace: 'pre-wrap', margin: '6px 0 0' }}>{v.body}</pre></details>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

/** On a bid: pick a template and get the letter filled in from this bid. */
export function UseTemplate({ bidId, tenderId }: { bidId: number; tenderId: string }) {
  const toast = useToast();
  const data = useLoad(() => api.letterTemplates(), []);
  const [id, setId] = useState<number | null>(null);
  const [text, setText] = useState('');
  const [missing, setMissing] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const list: LetterTemplate[] = data.data?.templates ?? [];
  async function fill(tid: number) {
    setId(tid); setBusy(true);
    try { const r = await api.fillLetter(bidId, tid); setText(r.text); setMissing(r.missing); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function copy() { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Could not copy', 'bad'); } }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = `${tenderId || 'bid'}-letter.txt`; a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Letter templates</h2>
      <p className="sub">Your own wording, filled in from this bid and your company facts. No AI needed. Templates are kept in the <a href="#/vault">Vault</a>.</p>
      {data.loading && !data.data && <Skeleton h={40} />}
      {data.data && list.length === 0 && <p className="muted small">No templates yet. Add one in the Vault, under Letters.</p>}
      {list.length > 0 && (
        <div className="seg" role="group" aria-label="Template" style={{ flexWrap: 'wrap', marginBottom: 10 }}>
          {list.map((t) => <button key={t.id} type="button" className={id === t.id ? 'on' : ''} aria-pressed={id === t.id} disabled={busy} onClick={() => fill(t.id)}>{t.name}</button>)}
        </div>
      )}
      {text && (
        <>
          {missing.length > 0 && <div className="alert warn">No value yet for: {missing.map((m) => `{{${m}}}`).join(', ')}. They are left in the letter. Fill the bid's details or add the company fact, then choose the template again.</div>}
          <textarea className="draft-box" value={text} onChange={(e) => setText(e.target.value)} spellCheck aria-label="The letter" />
          <div className="d-actions"><button type="button" className="btn" onClick={copy}>Copy</button><button type="button" className="btn" onClick={download}>Download .txt</button></div>
        </>
      )}
    </section>
  );
}
