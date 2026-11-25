import { useState } from 'react';
import { api } from './api';
import { tr } from './i18n';
import { Only, useSession } from './session';
import { useToast } from './ui';

/** Starts reading the portals for tenders in one click, then shows the run's progress. If a run is already going it just shows that one. */
export function CollectButton({ portals, go, primary = true, label }: { portals: string[]; go: (tab: 'runs') => void; primary?: boolean; label?: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function start() {
    setBusy(true);
    try {
      await api.startRun(portals, false);
      toast(tr('Started. The first run takes 20 to 40 minutes and you can keep working. Its progress is on the Runs page.'));
    } catch (e) {
      // "a run is already in progress" is not a problem to report: the person wants to see it
      if (!/already|progress|running/i.test((e as Error).message)) { toast((e as Error).message, 'bad'); setBusy(false); return; }
    }
    setBusy(false);
    go('runs');
  }
  return <button type="button" className={`btn ${primary ? 'primary' : ''}`} onClick={start} disabled={busy || portals.length === 0}>{busy ? <><span className="spinner" /> {tr('Starting…')}</> : (label ?? tr('Collect tenders now'))}</button>;
}

/** What a brand-new installation shows where tenders would be: what to do, how long it takes, and a way to look around first. */
export function NothingCollected({ portals, go, onSample }: { portals: string[]; go: (tab: 'runs') => void; onSample?: () => void }) {
  const { can } = useSession();
  return (
    <div className="card empty" style={{ padding: 40 }}>
      <svg className="empty-ico" viewBox="0 0 24 24" aria-hidden><path d="M11 4a7 7 0 105.2 11.7l3.5 3.5 1.4-1.4-3.5-3.5A7 7 0 0011 4zm0 2a5 5 0 110 10 5 5 0 010-10z" /></svg>
      <h2 style={{ marginBottom: 6 }}>{tr('No tenders collected yet')}</h2>
      <p className="sub" style={{ margin: '0 auto 16px', maxWidth: 520 }}>
        {can('bidder')
          ? tr('TendAI reads the government portals for civil tenders. Press the button to start. The first run takes 20 to 40 minutes and you can keep working while it goes; after that it collects by itself every day.')
          : tr('No tenders have been collected yet. Ask an administrator or bidder to start the first collection.')}
      </p>
      <Only role="bidder">
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
          <CollectButton portals={portals} go={go} />
          {onSample && can('admin') && <button type="button" className="btn" onClick={onSample}>{tr('Look around with sample tenders first')}</button>}
        </div>
      </Only>
    </div>
  );
}
