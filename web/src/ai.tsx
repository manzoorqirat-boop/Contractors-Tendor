import { Fragment, useEffect, useState } from 'react';
import { api } from './api';
import { fromParams } from './filters';
import { fmtDate, inr } from './format';
import { useToast } from './ui';
import type { Nav } from './App';
import { DocumentEvidence } from './Clauses';
import type { AiStatus, Brief, DocFacts, Filters, TenderDetail } from './types';
import { tr } from './i18n';

/** Asked once per page load: lets the AI sections stay out of the way when no key is set. */
let statusPromise: Promise<AiStatus | null> | null = null;
export function useAiStatus(): AiStatus | null {
  const [s, setS] = useState<AiStatus | null>(null);
  useEffect(() => {
    statusPromise ??= api.aiStatus().catch(() => null);
    let live = true;
    statusPromise.then((r) => { if (live) setS(r); });
    return () => { live = false; };
  }, []);
  return s;
}
export const resetAiStatus = () => { statusPromise = null; };

export function FitBadge({ fit, reason, notCivil }: { fit?: number | null; reason?: string | null; notCivil?: number | null }) {
  return (
    <>
      {fit ? <span className={`badge ${fit >= 4 ? 'ok' : fit === 3 ? 'warn' : 'plain'}`} title={reason || undefined}>Fit {fit}/5</span> : null}
      {notCivil === 0 && <span className="badge warn" title="The AI thinks this may not be civil work. Check before relying on it.">May not be civil</span>}
    </>
  );
}

/** One question in plain words becomes filters on the Tenders page. */
export function AskBar({ nav }: { nav: Nav }) {
  const toast = useToast();
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    try {
      const r = await api.ask(q.trim());
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(r.filters)) { if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x))); else if (v === true) p.set(k, 'true'); else p.set(k, String(v)); }
      const f: Filters = fromParams(p).filters;
      if (!r.filters.status) f.status = 'open';
      nav.go('tenders', f, r.sort ? { sort: r.sort } : undefined);
      toast(r.explanation || 'Showing matching tenders');
    } catch (err) { toast((err as Error).message, 'bad'); }
    finally { setBusy(false); }
  }
  return (
    <form className="askbar" onSubmit={submit} role="search">
      <span className="askbar-ai" aria-hidden>AI</span>
      <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="Ask in plain words, e.g. drainage tenders in Pune over 50 lakh closing this week" aria-label="Ask TendAI about tenders" />
      <button className="btn ai primary" disabled={busy || !q.trim()}>{busy ? 'Thinking…' : 'Ask'}</button>
    </form>
  );
}

const List = ({ title, items }: { title: string; items: string[] }) => (items.length ? <div className="ai-sec"><h4>{title}</h4><ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div> : null);

function DocView({ doc }: { doc: DocFacts }) {
  return (
    <div className="ai-text">
      {doc.summary && <p style={{ margin: '0 0 8px' }}>{doc.summary}</p>}
      <List title="Who can bid" items={doc.requirements} />
      {doc.financial.length > 0 && <div className="ai-sec"><h4>Money</h4><dl className="facts">{doc.financial.map((f, i) => <Fragment key={i}><dt>{f.label}</dt><dd>{f.value}</dd></Fragment>)}</dl></div>}
      {doc.dates.length > 0 && <div className="ai-sec"><h4>Dates</h4><dl className="facts">{doc.dates.map((f, i) => <Fragment key={i}><dt>{f.label}</dt><dd>{f.value}</dd></Fragment>)}</dl></div>}
      <List title="Documents to submit" items={doc.documents} />
      <List title="Penalties" items={doc.penalties} />
      {doc.red_flags.length > 0 && <div className="alert warn" style={{ marginTop: 8 }}><strong>Watch out</strong><ul style={{ margin: '4px 0 0 18px', padding: 0 }}>{doc.red_flags.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
      <DocumentEvidence doc={doc} found={null} />
    </div>
  );
}

export function BriefView({ b }: { b: Brief }) {
  const tone = b.recommendation === 'Bid' ? 'ok' : b.recommendation === 'No bid' ? 'bad' : 'warn';
  return (
    <div className="ai-text">
      <div className="brief-head"><span className={`badge ${tone}`}>{b.recommendation}</span><strong>{b.headline}</strong></div>
      <List title="Why" items={b.reasons} />
      <List title="Risks" items={b.risks} />
      <List title="Confirm before bidding" items={b.checks} />
    </div>
  );
}

export const readBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result).split(',')[1] ?? '');
  r.onerror = () => reject(new Error('Could not read that file'));
  r.readAsDataURL(file);
});

/** Everything the AI assistant can do for one open tender. Shown only when an API key is set. */
export function AiSections({ t, onChange }: { t: TenderDetail; onChange: (patch: Partial<TenderDetail>) => void }) {
  const status = useAiStatus();
  const [summary, setSummary] = useState(t.summary ?? '');
  const [brief, setBrief] = useState<Brief | null>(t.brief);
  const [doc, setDoc] = useState<DocFacts | null>(t.doc);
  const [busy, setBusy] = useState<'summary' | 'brief' | 'doc' | null>(null);
  const [error, setError] = useState('');
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [showDoc, setShowDoc] = useState(false);

  useEffect(() => { setSummary(t.summary ?? ''); setBrief(t.brief); setDoc(t.doc); setError(''); setText(''); setFile(null); setShowDoc(false); }, [t.key]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!status?.configured) return null;

  async function run<T>(what: 'summary' | 'brief' | 'doc', fn: () => Promise<T>, done: (r: T) => void) {
    setBusy(what); setError('');
    try { done(await fn()); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }
  const summarise = () => run('summary', () => api.summary(t.key, !!summary), (r) => { setSummary(r.summary); onChange({ summary: r.summary }); });
  const makeBrief = () => run('brief', () => api.brief(t.key, !!brief), (r) => { setBrief(r.brief); onChange({ brief: r.brief }); });
  const readDoc = () => run('doc', async () => api.document(t.key, file ? '' : text, file ? await readBase64(file) : ''), (r) => { setDoc(r.doc); onChange({ doc: r.doc }); setShowDoc(false); });

  return (
    <div className="ai-box">
      <div className="ai-title"><span className="askbar-ai" aria-hidden>AI</span> TendAI assistant</div>
      <div className="ai-actions">
        <button className="btn ai" onClick={summarise} disabled={busy !== null}>{busy === 'summary' ? 'Reading…' : summary ? 'Summarise again' : 'Summarise'}</button>
        <button className="btn ai" onClick={makeBrief} disabled={busy !== null}>{busy === 'brief' ? 'Thinking…' : brief ? 'Refresh bid brief' : 'Bid / no-bid brief'}</button>
        <button className="btn ai" onClick={() => setShowDoc((v) => !v)} disabled={busy !== null} aria-expanded={showDoc}>{doc ? tr('Read another document') : 'Read tender document'}</button>
      </div>
      {error && <div className="alert warn" style={{ marginTop: 10 }}>{error}</div>}
      {showDoc && (
        <div className="ai-doc">
          <label className="field">Upload the tender PDF (up to 10 MB)<input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="field">Or paste the text<textarea value={text} onChange={(e) => setText(e.target.value)} disabled={!!file} placeholder="Paste the NIT or key pages here" spellCheck={false} /></label>
          <button className="btn ai primary" onClick={readDoc} disabled={busy !== null || (!file && !text.trim())}>{busy === 'doc' ? tr('Reading document…') : 'Extract key facts'}</button>
        </div>
      )}
      {brief && <><h4 className="ai-h">Bid / no-bid brief</h4><BriefView b={brief} /></>}
      {doc && <><h4 className="ai-h">From the document</h4><DocView doc={doc} /></>}
      {summary && <><h4 className="ai-h">Summary</h4><div className="ai-text" style={{ whiteSpace: 'pre-wrap' }}>{summary}</div></>}
      {(summary || brief || doc) && <p className="muted small" style={{ marginTop: 8 }}>AI can be wrong. Check every figure and date on the portal before bidding.</p>}
    </div>
  );
}

export function RelatedNote({ t }: { t: TenderDetail }) {
  if (!t.related?.length) return null;
  return (
    <div className="alert warn" style={{ marginTop: 14 }}>
      <strong>Looks like the same work was tendered before or again</strong>
      <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
        {t.related.map((r) => <li key={r.key}><span className="mono">{r.tender_id}</span> · found {fmtDate(r.first_seen)} · {r.closing ? `closes ${fmtDate(r.closing)}` : 'no closing date'}{r.value !== null && ` · ${inr(r.value, false)}`}</li>)}
      </ul>
    </div>
  );
}
