import { useState } from 'react';
import { api } from './api';
import { readBase64 } from './ai';
import { EvidenceBadge } from './Clauses';
import { useToast } from './ui';
import type { BidDetail, CorrChange, CorrigendumRead } from './types';

const IMPACT: Record<CorrChange['impact'], { label: string; tone: string }> = {
  price: { label: 'Affects your price', tone: 'bad' }, eligibility: { label: 'Affects who can bid', tone: 'bad' }, dates: { label: 'Dates', tone: 'warn' },
  documents: { label: 'Documents to submit', tone: 'warn' }, other: { label: 'Other', tone: 'plain' },
};

/** Reads a corrigendum for you: what it changes. Fixed rules compare its conditions with the tender's (no AI); with the AI assistant on it also lists every change and what to do. */
function CorrigendumReader({ bidId, onUse }: { bidId: number; onUse: (note: string, closing: string) => void }) {
  const toast = useToast();
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<CorrigendumRead | null>(null);

  async function read() {
    setBusy(true); setOut(null);
    try { setOut(await api.readCorrigendum(bidId, file ? '' : text, file ? await readBase64(file) : '')); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }

  function use() {
    if (!out) return;
    const lines = out.ai?.changes.length ? out.ai.changes.map((c) => `${c.what}${c.new ? `: ${c.new}` : ''}`) : out.rules.map((c) => `${c.label}: ${c.old ? `${c.old} to ` : ''}${c.new}`);
    onUse(((out.ai?.summary ? `${out.ai.summary} ` : '') + lines.join('; ')).slice(0, 500), out.ai?.closing ?? '');
  }

  const nothing = out && out.rules.length === 0 && (!out.ai || out.ai.changes.length === 0);
  return (
    <div style={{ display: 'grid', gap: 8, marginBottom: 12 }}>
      <label className="field" style={{ margin: 0 }}>Paste the corrigendum text, or choose its PDF
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} disabled={!!file} spellCheck={false} aria-label="Corrigendum text" />
      </label>
      <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Corrigendum PDF" />
      <div><button type="button" className="btn small" onClick={read} disabled={busy || (!file && !text.trim())}>{busy ? 'Reading…' : 'Read it for me'}</button></div>
      {out && (
        <div role="status">
          {out.ai?.summary && <p style={{ margin: '4px 0' }}><strong>{out.ai.summary}</strong></p>}
          {out.ai_error && <div className="alert warn">The AI assistant could not read it ({out.ai_error}). The fixed rules found the changes below.</div>}
          {!out.had_document && out.rules.length > 0 && <p className="muted small">The tender's own conditions have not been read yet, so these are conditions the corrigendum states, not proven differences.</p>}
          {out.ai && out.ai.changes.length > 0 && (
            <ul style={{ listStyle: 'none', margin: '6px 0', padding: 0 }}>
              {out.ai.changes.map((c, i) => (
                <li key={i} style={{ marginBottom: 8 }}>
                  <div><span className={`badge ${IMPACT[c.impact].tone}`}>{IMPACT[c.impact].label}</span> <strong>{c.what}</strong>{(c.old || c.new) && <>: {c.old && <s>{c.old}</s>} {c.new}</>}</div>
                  {c.action && <div className="small">What to do: {c.action}</div>}
                  {c.quote && <blockquote className="quote">“{c.quote}” <EvidenceBadge verified={c.verified} /></blockquote>}
                </li>
              ))}
            </ul>
          )}
          {out.rules.length > 0 && (
            <>
              <h3 style={{ margin: '8px 0 4px' }}>Conditions that differ or are new (found by fixed rules)</h3>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {out.rules.map((c) => <li key={c.kind} style={{ marginBottom: 6 }}><strong>{c.label}</strong>: {c.old ? <><s>{c.old}</s> {c.new}</> : <>{c.new} <span className="muted small">(new)</span></>}<blockquote className="quote">“{c.quote}”</blockquote></li>)}
              </ul>
            </>
          )}
          {nothing && <p className="muted">{out.has_text ? 'No changes to the usual conditions were found by the rules' : 'No text could be read from that file (it may be a scan). Paste the text instead'}{out.ai ? ', and the AI listed none.' : '. Turn on the AI assistant for a fuller reading.'}</p>}
          {(out.rules.length > 0 || (out.ai?.changes.length ?? 0) > 0) && <button type="button" className="btn small primary" onClick={use}>Use this as the note{out.ai?.closing ? ' and new closing date' : ''}</button>}
        </div>
      )}
    </div>
  );
}

/** Writes down a corrigendum read on the portal, with the closing date it set. The only way to log one on a tender the app cannot watch. */
export function CorrigendumForm({ bidId, manual, onRecorded }: { bidId: number; manual: boolean; onRecorded: (bid: BidDetail) => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [closing, setClosing] = useState('');
  const [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      onRecorded(await api.recordCorrigendum(bidId, note, closing));
      toast(closing ? 'Corrigendum recorded and the closing date moved' : 'Corrigendum recorded');
      setNote(''); setClosing(''); setOpen(false);
    } catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(false); }
  }
  if (!open) return <p style={{ margin: '8px 0 0' }}><button type="button" className="link-btn" onClick={() => setOpen(true)}>Record a corrigendum{manual ? ' you read on the portal' : ''}</button></p>;
  return (
    <form className="stack-fields" onSubmit={save} style={{ marginTop: 10, display: 'grid', gap: 8, maxWidth: 560 }}>
      <CorrigendumReader bidId={bidId} onUse={(n, c) => { setNote(n); setClosing(c); }} />
      <label className="field" style={{ margin: 0 }}>What did it change?
        <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={3} placeholder="e.g. Corrigendum 2: closing extended; BOQ item 12 revised" autoFocus />
      </label>
      <label className="field" style={{ margin: 0 }}>New closing date, if it moved (optional)
        <input type="datetime-local" value={closing} onChange={(e) => setClosing(e.target.value)} />
      </label>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn primary" disabled={busy || !note.trim()}>{busy ? 'Saving…' : 'Record'}</button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}
