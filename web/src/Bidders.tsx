import { useEffect, useState } from 'react';
import { api } from './api';
import { readBase64 } from './ai';
import { fmtDate, inr, parseMoney } from './format';
import { ScrollX } from './ScrollX';
import { Skeleton, useToast } from './ui';
import type { BidDetail, Bidder, Competitor } from './types';

interface Row { name: string; price: string; rank: string; status: string; mine: boolean }
const toRow = (b: Bidder): Row => ({ name: b.name, price: b.price === null ? '' : String(b.price), rank: b.rank === null ? '' : String(b.rank), status: b.status ?? '', mine: !!b.mine });

/** Who bid on this tender and at what price. Typed by hand, or read from an award notice for checking. */
export function BiddersCard({ bid, aiOn, onChange }: { bid: BidDetail; aiOn: boolean; onChange: (patch: Partial<BidDetail>) => void }) {
  const toast = useToast();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState('');
  const [show, setShow] = useState(false);
  const [note, setNote] = useState('');
  useEffect(() => { api.bidders(bid.id).then((r) => { setRows(r.bidders.map(toRow)); setDirty(false); }, () => setRows([])); }, [bid.id]);

  const set = (i: number, p: Partial<Row>) => { setRows((rs) => rs && rs.map((r, j) => (j === i ? { ...r, ...p } : p.mine ? { ...r, mine: false } : r))); setDirty(true); };
  const bad = (r: Row) => (r.price.trim() !== '' && parseMoney(r.price) === null ? 'Not an amount' : r.rank.trim() !== '' && !/^\d{1,2}$/.test(r.rank.trim()) ? 'Rank is a whole number' : '');
  const problems = (rows ?? []).filter((r) => r.name.trim() && bad(r)).length;

  async function save() {
    if (!rows) return;
    setBusy(true);
    try {
      const out = await api.saveBidders(bid.id, rows.filter((r) => r.name.trim()).map((r) => ({ name: r.name.trim(), price: parseMoney(r.price), rank: r.rank.trim() ? Number(r.rank) : null, status: r.status.trim(), mine: r.mine })));
      setRows(out.bidders.map(toRow)); setDirty(false);
      if (Object.keys(out.updated).length) { onChange(out.updated as Partial<BidDetail>); toast('Saved. Winning and your prices were filled in from the list.'); } else toast('Saved');
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function read() {
    setReading(true); setNote('');
    try {
      const { award } = await api.readAward(bid.id, file ? '' : paste, file ? await readBase64(file) : '');
      setRows(award.bidders.map(toRow)); setDirty(true); setShow(false); setFile(null); setPaste('');
      setNote(`Read ${award.bidders.length} bidder${award.bidders.length === 1 ? '' : 's'}${award.winner ? `. Winner named: ${award.winner}` : ''}${award.winning_price ? ` at ${inr(award.winning_price, false)}` : ''}${award.awarded ? `, awarded ${fmtDate(award.awarded)}` : ''}. ${award.notes} Check every name and price against the notice, mark your own row, then Save.`);
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setReading(false); }
  }

  if (rows === null) return <Skeleton h={100} />;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div><h2>Who bid</h2><p className="sub">Every bidder and their quote. It fills in the winning price and builds your record of competitors.</p></div>
        <div className="head-actions">{aiOn && <button type="button" className="btn small" onClick={() => setShow((v) => !v)} aria-expanded={show}>Read an award notice</button>}</div>
      </div>
      {show && (
        <div className="ai-doc">
          <label className="field">Upload the award notice or comparative statement (PDF, up to 10 MB)<input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="field">Or paste its text<textarea value={paste} onChange={(e) => setPaste(e.target.value)} disabled={!!file} spellCheck={false} /></label>
          <button type="button" className="btn primary" onClick={read} disabled={reading || (!file && !paste.trim())}>{reading ? 'Reading…' : 'Read it'}</button>
        </div>
      )}
      {note && <div className="alert warn" role="status">{note}</div>}
      {rows.length > 0 && (
        <ScrollX label="Bidders">
          <table className="tbl">
            <thead><tr><th>Company</th><th>Quote (₹)</th><th>Rank</th><th>Status</th><th>Us</th><th><span className="sr-only">Remove</span></th></tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const problem = r.name.trim() ? bad(r) : '';
                return (
                  <tr key={i}>
                    <td><input type="text" value={r.name} maxLength={120} aria-label={`Company ${i + 1}`} onChange={(e) => set(i, { name: e.target.value })} /></td>
                    <td><input type="text" inputMode="decimal" value={r.price} aria-label={`Quote of ${r.name || `company ${i + 1}`}`} aria-invalid={!!problem || undefined} onChange={(e) => set(i, { price: e.target.value })} style={{ minWidth: 130 }} />
                      {parseMoney(r.price) ? <span className="date-say">{inr(parseMoney(r.price))}</span> : null}{problem && <span className="field-err">{problem}</span>}</td>
                    <td><input type="text" inputMode="numeric" value={r.rank} aria-label={`Rank of ${r.name || `company ${i + 1}`}`} onChange={(e) => set(i, { rank: e.target.value })} style={{ width: 60 }} /></td>
                    <td><input type="text" value={r.status} maxLength={40} aria-label={`Status of ${r.name || `company ${i + 1}`}`} placeholder="qualified" onChange={(e) => set(i, { status: e.target.value })} /></td>
                    <td><input type="radio" name="mine" checked={r.mine} aria-label={`${r.name || `Company ${i + 1}`} is our company`} onChange={() => set(i, { mine: true })} /></td>
                    <td><button type="button" className="link-btn" aria-label={`Remove ${r.name || `company ${i + 1}`}`} onClick={() => { setRows((rs) => rs && rs.filter((_, j) => j !== i)); setDirty(true); }}>✕</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ScrollX>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <button type="button" className="btn small" onClick={() => { setRows((rs) => [...(rs ?? []), { name: '', price: '', rank: '', status: '', mine: false }]); setDirty(true); }} disabled={rows.length >= 30}>Add a bidder</button>
        <button type="button" className="btn small primary" onClick={save} disabled={busy || !dirty || problems > 0}>{busy ? 'Saving…' : 'Save bidders'}</button>
        {dirty && <span className="muted small">Not saved yet</span>}
      </div>
    </section>
  );
}

/** Companies that bid against you, across all tenders. */
export function CompetitorsCard() {
  const [rows, setRows] = useState<Competitor[] | null>(null);
  useEffect(() => { api.competitors().then((r) => setRows(r.competitors), () => setRows([])); }, []);
  if (rows === null) return <Skeleton h={80} />;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Competitors</h2>
      <p className="sub">Companies that bid against you, from the bidder lists on your bids. A quote below the estimate is a negative number.</p>
      {rows.length === 0 ? <p className="muted">Record who bid on a tender (Result tab, Who bid) and your competitors appear here.</p> : (
        <ScrollX label="Competitors">
          <table className="tbl">
            <thead><tr><th>Company</th><th className="num">Tenders</th><th className="num">Won</th><th className="num">Usually quotes</th><th>Mostly bids for</th><th>Last seen</th></tr></thead>
            <tbody>
              {rows.slice(0, 40).map((c) => (
                <tr key={c.key}>
                  <td><strong>{c.name}</strong></td><td className="num">{c.bids}</td><td className="num">{c.wins}</td>
                  <td className="num">{c.avg_vs_estimate === null ? <span className="muted">-</span> : `${c.avg_vs_estimate > 0 ? '+' : ''}${c.avg_vs_estimate}%`}{c.quotes > 0 && <div className="muted small">{c.quotes} quote{c.quotes === 1 ? '' : 's'}</div>}</td>
                  <td className="muted small">{c.departments.join(', ') || '-'}</td><td className="muted small">{c.last_seen ? fmtDate(c.last_seen) : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollX>
      )}
    </section>
  );
}
