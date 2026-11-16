import { useEffect, useState } from 'react';
import { api } from './api';
import { chain, fmtDate, headline, inr, relative } from './format';
import { AiSections, FitBadge, RelatedNote } from './ai';
import { Drawer, Skeleton, useToast } from './ui';
import { useSession } from './session';
import type { Tender, TenderDetail } from './types';
import { tr } from './i18n';

export function StarButton({ t, onChange }: { t: Pick<Tender, 'key' | 'starred'>; onChange: (starred: boolean) => void }) {
  const toast = useToast();
  const { can } = useSession();
  const [busy, setBusy] = useState(false);
  async function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    const next = !t.starred;
    onChange(next); // shown at once; put back if the server says no
    try { await api.star(t.key, next); }
    catch (err) { onChange(!next); toast((err as Error).message, 'bad'); }
    finally { setBusy(false); }
  }
  return <button type="button" className={`star${t.starred ? ' on' : ''}`} onClick={toggle} disabled={busy || !can('bidder')} aria-pressed={t.starred} aria-label={t.starred ? tr('Remove star') : tr('Star this tender')} title={t.starred ? tr('Starred') : tr('Star to keep an eye on it')}>{t.starred ? '★' : '☆'}</button>;
}

export function TenderDrawer({ tenderKey, now, onClose, onStar }: { tenderKey: string; now: string; onClose: () => void; onStar: (key: string, starred: boolean) => void }) {
  const toast = useToast();
  const { can } = useSession();
  const [t, setT] = useState<TenderDetail | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let stop = false;
    setT(null); setError('');
    api.tender(tenderKey).then((r) => { if (!stop) setT(r); }, (e: Error) => { if (!stop) setError(e.message); });
    return () => { stop = true; };
  }, [tenderKey]);

  async function startBid() {
    if (!t) return;
    try {
      const r = await api.createBid(t.key);
      if (r.created) toast(tr('Added to your bids'));
      onClose();
      window.dispatchEvent(new CustomEvent('open-bid', { detail: r.id }));
    } catch (err) { toast((err as Error).message, 'bad'); }
  }

  async function notInterested() {
    if (!t) return;
    try { await api.hideTenders([t.key], true); toast(tr('Hidden. Choose "Show ignored" in the list to bring it back.')); onClose(); window.dispatchEvent(new CustomEvent('tenders-changed')); }
    catch (err) { toast((err as Error).message, 'bad'); }
  }

  async function copy() {
    if (!t) return;
    try { await navigator.clipboard.writeText(t.tender_id); toast(tr('Tender ID copied')); } catch { toast(tr('Could not copy'), 'bad'); }
  }

  const rel = t ? relative(t.closing, now) : null;
  return (
    <Drawer title={tr('Tender details')} onClose={onClose}>
      {error && <div className="alert bad">{error}</div>}
      {!t && !error && <><Skeleton h={22} style={{ marginBottom: 10 }} /><Skeleton h={14} w="70%" style={{ marginBottom: 20 }} /><Skeleton h={160} /></>}
      {t && (
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <StarButton t={t} onChange={(s) => { setT({ ...t, starred: s }); onStar(t.key, s); }} />
            <h3 className="d-title">{headline(t)}</h3>
          </div>
          <p className="muted" style={{ margin: '6px 0 14px' }}>{chain(t.organisation).join(' › ')}</p>
          {rel?.text && <div className={`when-banner ${rel.tone}`}>{rel.text}</div>}
          {(t.work_type || t.fit || t.ai_civil === 0) && (
            <div className="chips" style={{ margin: '0 0 12px' }}>
              {t.work_type && <span className="badge accent">{t.work_type}</span>}
              <FitBadge fit={t.fit} notCivil={t.ai_civil} />
              {t.fit_reason && <span className="muted small">{t.fit_reason}</span>}
            </div>
          )}
          <RelatedNote t={t} />
          <dl className="facts">
            <dt>{tr('Estimated value')}</dt><dd><strong>{t.value === null ? tr('Not stated') : inr(t.value, false)}</strong>{t.value !== null && t.value >= 1e5 && <span className="muted"> ({inr(t.value)})</span>}</dd>
            <dt>{tr('EMD')}</dt><dd>{t.emd ? inr(t.emd, false) : '-'}</dd>
            <dt>{tr('Closes')}</dt><dd>{fmtDate(t.closing, true)}</dd>
            <dt>{tr('Bid opening')}</dt><dd>{fmtDate(t.opening, true)}</dd>
            <dt>{tr('Published')}</dt><dd>{fmtDate(t.published, true)}</dd>
            <dt>{tr('First found')}</dt><dd>{fmtDate(t.first_seen, true)}</dd>
            <dt>{tr('Portal')}</dt><dd>{t.portal_name}</dd>
            <dt>{tr('Category')}</dt><dd>{t.category || '-'}</dd>
            <dt>{tr('Location')}</dt><dd>{t.location || '-'}</dd>
            <dt>{tr('Tender ID')}</dt><dd className="mono">{t.tender_id}</dd>
          </dl>
          <div className="d-actions">
            <button className="btn" onClick={copy}>{tr('Copy Tender ID')}</button>
            {t.search_url && <a className="btn" href={t.search_url} target="_blank" rel="noopener noreferrer">{tr('Find on portal')}</a>}
            {t.manual && <button className="btn" onClick={() => { onClose(); window.dispatchEvent(new CustomEvent('add-tender', { detail: { editKey: t.key } })); }}>{tr('Edit details')}</button>}
            {(t.bid_id || can('bidder')) && <button className="btn primary" onClick={startBid}>{t.bid_id ? tr('Open bid workspace') : tr('Start a bid')}</button>}
            {can('bidder') && !t.bid_id && <button className="btn" onClick={notInterested} title={tr('Hide it from the list, the counts and the daily email')}>{tr('Not interested')}</button>}
          </div>
          {t.history && t.history.length > 0 && <div className="alert warn" style={{ marginTop: 12 }}>{t.history.map((h) => <div key={h}>{h}</div>)}</div>}
          <AiSections t={t} onChange={(patch) => setT((cur) => (cur ? { ...cur, ...patch } : cur))} />
          <p className="muted small-note">{t.manual ? 'Added by hand from its notice. The app cannot see corrigenda on it, so confirm every detail on the portal before bidding.' : "Portal links expire within minutes, so paste the Tender ID into the portal's search. Confirm every detail on the portal before bidding."}</p>
        </>
      )}
    </Drawer>
  );
}
