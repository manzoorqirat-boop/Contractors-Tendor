import { useState } from 'react';
import { api, ApiError } from './api';
import { useConfirm } from './confirm';
import { fmtDate, inr, parseMoney } from './format';
import { useLoad } from './hooks';
import { Only, useSession } from './session';
import { ErrorNote, FieldIssueCtx, FieldMsg, Skeleton, useToast, type FieldIssue } from './ui';
import type { BidMoney, MoneyFlag, MoneyItem, MoneySummary } from './types';
import { tr } from './i18n';

const KINDS: [string, string][] = [
  ['emd', tr('Earnest money (EMD)')], ['tender_fee', tr('Tender document fee')], ['processing_fee', tr('Processing or portal fee')],
  ['performance_security', tr('Performance security')], ['security_deposit', tr('Security deposit or retention')], ['other', tr('Other payment')],
];
const COMES_BACK = ['emd', 'performance_security', 'security_deposit'];
const INSTRUMENTS: [string, string][] = [['online', tr('Online (net banking, UPI, NEFT)')], ['dd', tr('Demand draft')], ['bg', tr('Bank guarantee')], ['fdr', tr('Fixed deposit (FDR)')], ['other', tr('Other')]];
const HAS_VALIDITY = ['bg', 'fdr', 'dd'];
const STATUSES: [string, string][] = [['planned', tr('Planned, not paid yet')], ['paid', tr('Paid')], ['refunded', tr('Refunded')], ['forfeited', tr('Forfeited')], ['adjusted', tr('Adjusted against another payment')]];
const label = (list: [string, string][], v: string) => list.find(([k]) => k === v)?.[1] ?? v;
const tone = (s: MoneyFlag['severity']) => (s === 'high' ? 'bad' : s === 'medium' ? 'warn' : 'plain');
const statusTone = (i: MoneyItem) => (i.locked ? 'warn' : i.status === 'refunded' ? 'ok' : i.status === 'forfeited' ? 'bad' : 'plain');

type Form = { kind: string; amount: string; instrument: string; status: string; reference: string; bank: string; paid_on: string; valid_until: string; refund_due: string; note: string };
const blank = (over: Partial<Form> = {}): Form => ({ kind: 'emd', amount: '', instrument: 'online', status: 'paid', reference: '', bank: '', paid_on: '', valid_until: '', refund_due: '', note: '', ...over });
const fromItem = (i: MoneyItem): Form => ({ kind: i.kind, amount: String(i.amount), instrument: i.instrument, status: i.status, reference: i.reference ?? '', bank: i.bank ?? '', paid_on: i.paid_on ?? '', valid_until: i.valid_until ?? '', refund_due: i.refund_due ?? '', note: i.note ?? '' });

function Totals({ t }: { t: BidMoney['totals'] | MoneySummary['totals'] }) {
  const cell = (name: string, value: number, hint: string, strong = false) => (
    <div title={hint} style={{ display: 'flex', flexDirection: 'column' }}><span className="muted small">{name}</span><strong style={{ fontSize: strong ? '1.25em' : undefined }}>{inr(value)}</strong></div>
  );
  return (
    <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', margin: '8px 0 12px' }}>
      {cell(tr('Cash locked in bids'), t.cash_locked, tr('Paid by net banking, draft or deposit, and not yet back'), true)}
      {cell(tr('Bank guarantees in use'), t.guarantees, tr('Guarantees issued by your bank and not yet released'), true)}
      {cell(tr('Fees paid'), t.fees_paid, tr('Tender and portal fees: these do not come back'))}
      {cell(tr('Refunded'), t.refunded, '')}
      {t.forfeited > 0 && cell(tr('Forfeited'), t.forfeited, tr('Money that was not returned'))}
    </div>
  );
}

function Flags({ flags }: { flags: MoneyFlag[] }) {
  return <>{flags.map((f) => <div key={f.code} className={`alert ${tone(f.severity) === 'plain' ? '' : tone(f.severity)}`} role="note" style={{ margin: '6px 0 0' }}>{f.text}</div>)}</>;
}

/** Record or change one payment. */
function PaymentForm({ initial, onSave, onCancel, busy, issue }: { initial: Form; onSave: (f: Form) => void; onCancel: () => void; busy: boolean; issue: FieldIssue }) {
  const [f, setF] = useState(initial);
  const set = (k: keyof Form, v: string) => setF((x) => ({ ...x, [k]: v }));
  return (
    <FieldIssueCtx.Provider value={issue}>
    <div className="card" style={{ margin: '10px 0', background: 'var(--bg-soft, transparent)' }} onKeyDown={(e) => { if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT' && !busy) { e.preventDefault(); onSave(f); } }}>
      <div className="set-grid">
        <label className="field">{tr('What it is for')}
          <select value={f.kind} onChange={(e) => set('kind', e.target.value)}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select><FieldMsg name="kind" />
        </label>
        <label className="field">{tr('Amount (₹)')}
          <input value={f.amount} onChange={(e) => set('amount', e.target.value)} inputMode="decimal" placeholder="50,000" required aria-invalid={issue?.field === 'amount' ? true : undefined} /><FieldMsg name="amount" />
        </label>
        <label className="field">{tr('How it was paid')}
          <select value={f.instrument} onChange={(e) => set('instrument', e.target.value)}>{INSTRUMENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select><FieldMsg name="instrument" />
        </label>
        <label className="field">{tr('Status')}
          <select value={f.status} onChange={(e) => set('status', e.target.value)}>{STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        </label>
        <label className="field">{tr('Reference (draft, guarantee or receipt number)')}
          <input value={f.reference} onChange={(e) => set('reference', e.target.value)} maxLength={60} /><FieldMsg name="reference" />
        </label>
        <label className="field">{tr('Bank')}
          <input value={f.bank} onChange={(e) => set('bank', e.target.value)} maxLength={60} /><FieldMsg name="bank" />
        </label>
        <label className="field">{tr('Paid on')}
          <input type="date" value={f.paid_on} onChange={(e) => set('paid_on', e.target.value)} /><FieldMsg name="paid_on" />
        </label>
        {HAS_VALIDITY.includes(f.instrument) && (
          <label className="field">{tr('Valid until')}
            <span className="hint">{tr('When the guarantee, draft or deposit runs out. You are reminded 30 days before.')}</span>
            <input type="date" value={f.valid_until} onChange={(e) => set('valid_until', e.target.value)} /><FieldMsg name="valid_until" />
          </label>
        )}
        {COMES_BACK.includes(f.kind) && (
          <label className="field">{tr('Refund expected by')}
            <span className="hint">{tr('You are reminded when this date passes and the money is still held.')}</span>
            <input type="date" value={f.refund_due} onChange={(e) => set('refund_due', e.target.value)} /><FieldMsg name="refund_due" />
          </label>
        )}
        <label className="field">{tr('Note')}
          <input value={f.note} onChange={(e) => set('note', e.target.value)} maxLength={300} /><FieldMsg name="note" />
        </label>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button type="button" className="btn primary" onClick={() => onSave(f)} disabled={busy}>{busy ? <><span className="spinner" /> {tr('Saving…')}</> : tr('Save payment')}</button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>{tr('Cancel')}</button>
      </div>
    </div>
    </FieldIssueCtx.Provider>
  );
}

/** The bid's Money tab: what was paid, how, what is still held, and what to chase. */
export function MoneyTab({ bidId, onChanged }: { bidId: number; onChanged?: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const data = useLoad(() => api.bidMoney(bidId), [bidId]);
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const [seed, setSeed] = useState<Partial<Form>>({});
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState<FieldIssue>(null);
  const d = data.data;

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try { await fn(); data.reload(); setEditing(null); setIssue(null); toast(done); onChanged?.(); }
    catch (e) { if (e instanceof ApiError && e.field) setIssue({ field: e.field, message: e.message }); else toast((e as Error).message, 'bad'); }
    finally { setBusy(false); }
  }
  const payload = (f: Form) => {
    const amount = parseMoney(f.amount);
    return { kind: f.kind, amount, instrument: f.instrument, status: f.status, reference: f.reference, bank: f.bank, paid_on: f.paid_on || null, valid_until: f.valid_until || null, refund_due: f.refund_due || null, note: f.note };
  };
  const quick = (i: MoneyItem, status: string, say: string) => run(() => api.changeMoney(bidId, i.id, { status }), say);
  async function remove(i: MoneyItem) {
    if (!(await confirm({ title: tr('Delete this payment?'), body: tr('It is removed from the totals. Use this only for a mistake: to record a refund, change its status instead.'), confirmLabel: tr('Delete'), danger: true }))) return;
    await run(() => api.deleteMoney(bidId, i.id), tr('Payment deleted'));
  }

  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div>
          <h2>{tr('Money in this bid')}</h2>
          <p className="sub">{tr('Record what you pay for this bid and what should come back. The app reminds you when a refund is late or a guarantee is about to run out.')}</p>
        </div>
        <Only role="bidder"><button type="button" className="btn primary" onClick={() => { setSeed({}); setEditing('new'); }} disabled={editing !== null}>{tr('Add a payment')}</button></Only>
      </div>
      <ErrorNote error={data.error} onRetry={data.reload} />
      {data.loading && !d && <Skeleton h={60} />}
      {d && (
        <>
          <Totals t={d.totals} />
          {d.suggestion && editing === null && (
            <div className="alert warn" role="status" style={{ display: 'flex', gap: 10, justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap' }}>
              <span>{d.suggestion.text} ({tr('The tender asks for')} {inr(d.suggestion.amount)}.)</span>
              <Only role="bidder"><button type="button" className="btn small" onClick={() => { setSeed({ kind: 'emd', amount: String(d.suggestion!.amount), status: 'planned' }); setEditing('new'); }}>{tr('Record the EMD')}</button></Only>
            </div>
          )}
          {editing === 'new' && <PaymentForm issue={issue} initial={blank(seed)} busy={busy} onCancel={() => { setEditing(null); setIssue(null); }} onSave={(f) => run(() => api.addMoney(bidId, payload(f)), tr('Payment saved'))} />}
          {d.items.length === 0 && editing === null && !d.suggestion && <p className="muted">{tr('Nothing recorded yet.')}</p>}
          {d.items.map((i) => (
            <div key={i.id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
              {editing === i.id ? (
                <PaymentForm issue={issue} initial={fromItem(i)} busy={busy} onCancel={() => setEditing(null)} onSave={(f) => run(() => api.changeMoney(bidId, i.id, payload(f)), tr('Payment saved'))} />
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 10, justifyContent: 'space-between', flexWrap: 'wrap' }}>
                    <div>
                      <strong>{i.kind_label}</strong> <span className={`badge ${statusTone(i)}`}>{i.locked ? tr('Held') : label(STATUSES, i.status)}</span>
                      <div className="muted small">
                        {label(INSTRUMENTS, i.instrument)}{i.reference ? ` · ${i.reference}` : ''}{i.bank ? ` · ${i.bank}` : ''}
                        {i.paid_on ? ` · ${tr('paid')} ${fmtDate(i.paid_on)}` : ''}{i.valid_until ? ` · ${tr('valid until')} ${fmtDate(i.valid_until)}` : ''}
                        {i.refund_due && i.locked ? ` · ${tr('refund expected by')} ${fmtDate(i.refund_due)}` : ''}{i.settled_on ? ` · ${label(STATUSES, i.status).toLowerCase()} ${fmtDate(i.settled_on)}` : ''}
                      </div>
                      {i.note && <div className="small">{i.note}</div>}
                    </div>
                    <strong style={{ whiteSpace: 'nowrap' }}>{inr(i.amount, false)}</strong>
                  </div>
                  <Flags flags={i.flags} />
                  <Only role="bidder">
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                      {i.status === 'planned' && <button type="button" className="btn small" onClick={() => quick(i, 'paid', tr('Marked as paid'))} disabled={busy}>{tr('Mark paid')}</button>}
                      {i.locked && <button type="button" className="btn small" onClick={() => quick(i, 'refunded', tr('Marked as refunded'))} disabled={busy}>{tr('Mark refunded')}</button>}
                      {i.locked && <button type="button" className="btn small" onClick={() => quick(i, 'forfeited', tr('Marked as forfeited'))} disabled={busy}>{tr('Mark forfeited')}</button>}
                      <button type="button" className="btn small" onClick={() => setEditing(i.id)} disabled={busy || editing !== null}>{tr('Edit')}</button>
                      <button type="button" className="btn small" onClick={() => remove(i)} disabled={busy}>{tr('Delete')}</button>
                    </div>
                  </Only>
                </>
              )}
            </div>
          ))}
        </>
      )}
    </section>
  );
}

/** The Dashboard's view of all bids together: cash locked, guarantees in use, and what needs chasing. Shown once any payment is recorded, and only to people who may see it. */
export function MoneyCard({ onOpenBid }: { onOpenBid: (id: number) => void }) {
  const { can } = useSession();
  if (!can('bidder')) return null;
  return <MoneyCardInner onOpenBid={onOpenBid} />;
}

function MoneyCardInner({ onOpenBid }: { onOpenBid: (id: number) => void }) {
  const data = useLoad(() => api.moneySummary(), []);
  const d = data.data;
  if (!d || d.count === 0) return null;
  return (
    <section className="card" style={{ marginBottom: 14 }} aria-label={tr('Money in bids')}>
      <div className="card-head">
        <div>
          <h2>{tr('Money in bids')}</h2>
          <p className="sub">
            {d.refunds_overdue > 0 ? <><strong>{inr(d.refunds_overdue)}</strong> {tr('is overdue for refund')}. </> : null}
            {d.refunds_due_30_days > 0 ? <>{inr(d.refunds_due_30_days)} {tr('is expected back in the next 30 days')}.</> : d.refunds_overdue === 0 ? tr('No refund is overdue.') : null}
          </p>
        </div>
        <a className="btn small" href="/api/money/export" download>{tr('Download spreadsheet')}</a>
      </div>
      <Totals t={d.totals} />
      {d.attention.length > 0 && (
        <ul className="soon-list">
          {d.attention.slice(0, 6).map((a) => (
            <li key={`${a.money_id}-${a.code}`}>
              <button type="button" onClick={() => onOpenBid(a.bid_id)}>
                <span className="soon-main"><span className="strong">{a.text}</span><span className="muted small">{inr(a.amount, false)} · {a.title.slice(0, 80)} · ID {a.tender_id}</span></span>
                <span className={`when pill ${tone(a.severity)}`}>{a.severity === 'high' ? tr('Act today') : a.severity === 'medium' ? tr('This week') : tr('Soon')}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {d.attention.length > 6 && <p className="muted small">{`${d.attention.length - 6} `}{tr('more to look at in your bids')}</p>}
    </section>
  );
}
