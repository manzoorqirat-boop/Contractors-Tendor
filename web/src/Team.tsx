import { useEffect, useState } from 'react';
import { api } from './api';
import { useConfirm } from './confirm';
import { fmtDate, plural } from './format';
import { useSession } from './session';
import { useToast } from './ui';
import type { AppConfig, BidComment, BidDetail, Person } from './types';
import { tr } from './i18n';

let cached: Promise<Person[]> | null = null;
/** Everyone a task can be given to (names only). Loaded once, shared by every picker. */
export function usePeople(): Person[] {
  const [people, setPeople] = useState<Person[]>([]);
  useEffect(() => { cached ??= api.people().then((r) => r.people).catch(() => { cached = null; return []; }); cached.then(setPeople); }, []);
  return people;
}
export const resetPeople = () => { cached = null; };

/** A dropdown of people. A name typed before accounts existed is kept and shown as it was. */
export function AssigneeSelect({ value, onChange, label, className = 'mini-select', hideIfAlone = false }: { value: string | null; onChange: (v: string) => void; label: string; className?: string; hideIfAlone?: boolean }) {
  const people = usePeople();
  if (hideIfAlone && people.length <= 1 && !value) return null;   // with one person on the account there is nobody to hand an item to: no picker on every line
  const known = people.some((p) => p.username.toLowerCase() === (value ?? '').toLowerCase());
  return (
    <select className={className} value={value ?? ''} aria-label={label} onChange={(e) => onChange(e.target.value)}>
      <option value="">{tr('Unassigned')}</option>
      {value && !known && <option value={value}>{value}</option>}
      {people.map((p) => <option key={p.username} value={p.username}>{p.name}</option>)}
    </select>
  );
}

const when = (iso: string) => fmtDate(iso, true);

/** Open checklist items given to the signed-in person, soonest closing first. */
export function MyWork({ onOpen }: { onOpen: (bidId: number) => void }) {
  const [items, setItems] = useState<{ item_id: number; text: string; bid_id: number; title: string; description: string; tender_id: string; closing: string | null }[] | null>(null);
  useEffect(() => { api.myWork().then((r) => setItems(r.items), () => setItems([])); }, []);
  if (!items || items.length === 0) return null;
  return (
    <section className="card" style={{ marginBottom: 14 }} aria-label={tr('Assigned to me')}>
      <h2>Assigned to me <span className="count-pill">{items.length}</span></h2>
      <ul className="soon-list">
        {items.slice(0, 8).map((i) => (
          <li key={i.item_id}>
            <button type="button" onClick={() => onOpen(i.bid_id)}>
              <span className="soon-main"><span className="strong">{i.text}</span><span className="muted small">{(i.description || i.title).slice(0, 90)} · ID {i.tender_id}</span></span>
              {i.closing && <span className="when pill plain">Closes {fmtDate(i.closing)}</span>}
            </button>
          </li>
        ))}
      </ul>
      {items.length > 8 && <p className="muted small">and {items.length - 8} more in your bids</p>}
    </section>
  );
}

/** Approval to bid, and the discussion about this bid. */
export function TeamTab({ bid, config, onChange }: { bid: BidDetail; config: AppConfig; onChange: (patch: Partial<BidDetail>) => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const { user, can } = useSession();
  const people = usePeople();
  const nameOf = (u: string) => people.find((p) => p.username === u)?.name ?? u;
  const [comments, setComments] = useState<BidComment[] | null>(null);
  const [text, setText] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const so = bid.signoff;
  const load = () => api.comments(bid.id).then((r) => setComments(r.comments), () => setComments([]));
  useEffect(() => { load(); }, [bid.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function signoff(action: 'request' | 'approve' | 'decline' | 'withdraw') {
    setBusy(true);
    try { const r = await api.signoff(bid.id, action, note); onChange({ signoff: r.signoff }); setNote(''); toast(action === 'request' ? 'Approval asked. Administrators were told.' : action === 'withdraw' ? 'Request withdrawn' : `Bid ${action === 'approve' ? 'approved' : 'declined'}`); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function post(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try { await api.addComment(bid.id, text.trim()); setText(''); await load(); onChange({ comment_count: (comments?.length ?? 0) + 1 }); }
    catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function remove(c: BidComment) {
    if (!(await confirm({ title: 'Remove this comment?', body: 'It is deleted for everyone.', confirmLabel: tr('Remove'), danger: true }))) return;
    try { await api.deleteComment(bid.id, c.id); await load(); onChange({ comment_count: Math.max(0, (comments?.length ?? 1) - 1) }); } catch (e) { toast((e as Error).message, 'bad'); }
  }

  const tone = so?.state === 'approved' ? 'ok' : so?.state === 'declined' ? 'bad' : 'warn';
  return (
    <>
      <section className="card" style={{ marginBottom: 14 }}>
        <h2>Approval to bid {so && <span className={`badge ${tone}`} style={{ marginLeft: 6 }}>{so.state === 'requested' ? tr('Waiting for approval') : so.state === 'approved' ? 'Approved' : 'Declined'}</span>}</h2>
        <p className="sub">{config.require_signoff ? 'An administrator must approve a bid before it moves from Interested to Preparing.' : 'Approval is optional here. Ask a colleague or an administrator to confirm that the company should bid.'}</p>
        {so && <div className={`alert ${so.state === 'declined' ? 'bad' : so.state === 'approved' ? 'ok' : 'warn'}`}>
          {so.state === 'requested' ? <><strong>{nameOf(so.requested_by)}</strong> asked on {when(so.at)}.</> : <><strong>{nameOf(so.by)}</strong> {so.state === 'approved' ? 'approved' : 'declined'} this on {when(so.at)}.</>}
          {so.note && <div style={{ marginTop: 4 }}>“{so.note}”</div>}
        </div>}
        {(bid.stage === 'interested' || so) && (
          <>
            {(!so || so.state === 'declined' || (can('admin') && so.state === 'requested')) && <label className="field">{can('admin') ? 'Note (optional)' : 'Why should we bid? (optional)'}
              <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} /></label>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {bid.stage === 'interested' && !so && <button type="button" className="btn primary" disabled={busy} onClick={() => signoff('request')}>{tr('Ask for approval')}</button>}
              {bid.stage === 'interested' && so?.state === 'declined' && <button type="button" className="btn" disabled={busy} onClick={() => signoff('request')}>Ask again</button>}
              {can('admin') && so?.state !== 'approved' && <button type="button" className="btn primary" disabled={busy} onClick={() => signoff('approve')}>{tr('Approve')}</button>}
              {can('admin') && so?.state !== 'declined' && <button type="button" className="btn" disabled={busy} onClick={() => signoff('decline')}>{tr('Decline')}</button>}
              {so && (so.requested_by === user?.username || can('admin')) && <button type="button" className="link-btn" disabled={busy} onClick={() => signoff('withdraw')}>{so.state === 'requested' ? 'Withdraw the request' : 'Clear the decision'}</button>}
            </div>
          </>
        )}
        {!so && bid.stage !== 'interested' && <p className="muted small">This bid has moved past Interested, so approval is no longer asked for.</p>}
      </section>

      <section className="card" style={{ marginBottom: 14 }}>
        <h2>Discussion {comments && comments.length > 0 && <span className="count-pill">{comments.length}</span>}</h2>
        <p className="sub">Notes for the team. Write @name to tell someone by email; the bid's owner is told of every comment.</p>
        {comments === null ? <span className="spinner" aria-label={tr('Loading')} /> : comments.length === 0 ? <p className="muted">{tr('No comments yet.')}</p> : (
          <ul className="comments">
            {comments.map((c) => (
              <li key={c.id}>
                <div className="comment-head"><strong>{nameOf(c.username)}</strong><span className="muted small">{when(c.at)}</span>
                  {(c.username === user?.username || can('admin')) && <button type="button" className="link-btn small" aria-label={`Remove comment by ${nameOf(c.username)}`} onClick={() => remove(c)}>{tr('Remove')}</button>}</div>
                <p>{c.text}</p>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={post}>
          <label className="field">Add a comment
            <textarea rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. @meera the GST certificate is renewed, please attach it" />
          </label>
          <button className="btn primary" disabled={busy || !text.trim()}>{tr('Post comment')}</button>
          <span className="muted small" style={{ marginLeft: 10 }}>{plural(2000 - text.length, 'character')} left</span>
        </form>
      </section>
    </>
  );
}
