import { useEffect, useState } from 'react';
import { api } from './api';
import { readBase64 } from './ai';
import { fmtDate, inr, parseMoney } from './format';
import { tr } from './i18n';
import { ScrollX } from './ScrollX';
import { Skeleton, useToast } from './ui';
import type { BidDetail, Bidder, Competitor } from './types';

let nextRowId = 1;   // a row keeps its identity when another row above it is removed, so the cursor stays where the person was typing
interface Row { uid: number; name: string; price: string; rank: string; status: string; mine: boolean }
const blankRow = (): Row => ({ uid: nextRowId++, name: '', price: '', rank: '', status: '', mine: false });
const toRow = (b: Bidder): Row => ({ uid: nextRowId++, name: b.name, price: b.price === null ? '' : String(b.price), rank: b.rank === null ? '' : String(b.rank), status: b.status ?? '', mine: !!b.mine });

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
  const bad = (r: Row) => (r.price.trim() !== '' && parseMoney(r.price) === null ? tr('Not an amount') : r.rank.trim() !== '' && !/^\d{1,2}$/.test(r.rank.trim()) ? tr('Rank is a whole number') : '');
  const problems = (rows ?? []).filter((r) => r.name.trim() && bad(r)).length;

  async function save() {
    if (!rows) return;
    setBusy(true);
    try {
      const out = await api.saveBidders(bid.id, rows.filter((r) => r.name.trim()).map((r) => ({ name: r.name.trim(), price: parseMoney(r.price), rank: r.rank.trim() ? Number(r.rank) : null, status: r.status.trim(), mine: r.mine })));
      setRows(out.bidders.map(toRow)); setDirty(false);
      if (Object.keys(out.updated).length) { onChange(out.updated as Partial<BidDetail>); toast(tr('Saved. Winning and your prices were filled in from the list.')); } else toast(tr('Saved'));
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function read() {
    setReading(true); setNote('');
    try {
      const { award } = await api.readAward(bid.id, file ? '' : paste, file ? await readBase64(file) : '');
      setRows(award.bidders.map(toRow)); setDirty(true); setShow(false); setFile(null); setPaste('');
      setNote(`${award.bidders.length === 1 ? tr('Read {n} bidder', { n: 1 }) : tr('Read {n} bidders', { n: award.bidders.length })}${award.winner ? `. ${tr('Winner named: {name}', { name: award.winner })}` : ''}${award.winning_price ? ` ${tr('at {amount}', { amount: inr(award.winning_price, false) })}` : ''}${award.awarded ? `, ${tr('awarded {date}', { date: fmtDate(award.awarded) })}` : ''}. ${award.notes} ${tr('Check every name and price against the notice, mark your own row, then Save.')}`);
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setReading(false); }
  }

  if (rows === null) return <Skeleton h={100} />;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div><h2>{tr('Who bid')}</h2><p className="sub">{tr('Every bidder and their quote. It fills in the winning price and builds your record of competitors.')}</p></div>
        <div className="head-actions">{aiOn && <button type="button" className="btn small" onClick={() => setShow((v) => !v)} aria-expanded={show}>{tr('Read an award notice')}</button>}</div>
      </div>
      {show && (
        <div className="ai-doc">
          <label className="field">{tr('Upload the award notice or comparative statement (PDF, up to 10 MB)')}<input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="field">{tr('Or paste its text')}<textarea value={paste} onChange={(e) => setPaste(e.target.value)} disabled={!!file} spellCheck={false} /></label>
          <button type="button" className="btn primary" onClick={read} disabled={reading || (!file && !paste.trim())}>{reading ? tr('Reading…') : tr('Read it')}</button>
        </div>
      )}
      {note && <div className="alert warn" role="status">{note}</div>}
      {rows.length > 0 && (
        <ScrollX label={tr('Bidders')}>
          <table className="tbl">
            <thead><tr><th>{tr('Company')}</th><th>{tr('Quote (₹)')}</th><th>{tr('Rank')}</th><th>{tr('Status')}</th><th>{tr('Us')}</th><th><span className="sr-only">{tr('Remove')}</span></th></tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const problem = r.name.trim() ? bad(r) : '';
                return (
                  <tr key={r.uid}>
                    <td><input type="text" value={r.name} maxLength={120} aria-label={tr('Company {n}', { n: i + 1 })} onChange={(e) => set(i, { name: e.target.value })} /></td>
                    <td><input type="text" inputMode="decimal" value={r.price} aria-label={tr('Quote of {name}', { name: r.name || tr('company {n}', { n: i + 1 }) })} aria-invalid={!!problem || undefined} onChange={(e) => set(i, { price: e.target.value })} style={{ minWidth: 130 }} />
                      {parseMoney(r.price) ? <span className="date-say">{inr(parseMoney(r.price))}</span> : null}{problem && <span className="field-err">{problem}</span>}</td>
                    <td><input type="text" inputMode="numeric" value={r.rank} aria-label={tr('Rank of {name}', { name: r.name || tr('company {n}', { n: i + 1 }) })} onChange={(e) => set(i, { rank: e.target.value })} style={{ width: 60 }} /></td>
                    <td><input type="text" value={r.status} maxLength={40} aria-label={tr('Status of {name}', { name: r.name || tr('company {n}', { n: i + 1 }) })} placeholder={tr('qualified')} onChange={(e) => set(i, { status: e.target.value })} /></td>
                    <td><input type="radio" name="mine" checked={r.mine} aria-label={tr('{name} is our company', { name: r.name || tr('Company {n}', { n: i + 1 }) })} onChange={() => set(i, { mine: true })} /></td>
                    <td><button type="button" className="link-btn" aria-label={tr('Remove {name}', { name: r.name || tr('company {n}', { n: i + 1 }) })} onClick={() => { setRows((rs) => rs && rs.filter((_, j) => j !== i)); setDirty(true); }}>✕</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ScrollX>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <button type="button" className="btn small" onClick={() => { setRows((rs) => [...(rs ?? []), blankRow()]); setDirty(true); }} disabled={rows.length >= 30}>{tr('Add a bidder')}</button>
        <button type="button" className="btn small primary" onClick={save} disabled={busy || !dirty || problems > 0}>{busy ? tr('Saving…') : tr('Save bidders')}</button>
        {dirty && <span className="muted small">{tr('Not saved yet')}</span>}
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
      <h2>{tr('Competitors')}</h2>
      <p className="sub">{tr('Companies that bid against you, from the bidder lists on your bids. A quote below the estimate is a negative number.')}</p>
      {rows.length === 0 ? <p className="muted">{tr('Record who bid on a tender (Result tab, Who bid) and your competitors appear here.')}</p> : (
        <ScrollX label={tr('Competitors')}>
          <table className="tbl">
            <thead><tr><th>{tr('Company')}</th><th className="num">{tr('Tenders')}</th><th className="num">{tr('Won')}</th><th className="num">{tr('Usually quotes')}</th><th>{tr('Mostly bids for')}</th><th>{tr('Last seen')}</th></tr></thead>
            <tbody>
              {rows.slice(0, 40).map((c) => (
                <tr key={c.key}>
                  <td><strong>{c.name}</strong></td><td className="num">{c.bids}</td><td className="num">{c.wins}</td>
                  <td className="num">{c.avg_vs_estimate === null ? <span className="muted">-</span> : `${c.avg_vs_estimate > 0 ? '+' : ''}${c.avg_vs_estimate}%`}{c.quotes > 0 && <div className="muted small">{c.quotes === 1 ? tr('{n} quote', { n: 1 }) : tr('{n} quotes', { n: c.quotes })}</div>}</td>
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
