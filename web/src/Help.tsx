import { useEffect, useState } from 'react';
import { tr } from './i18n';
import { Drawer } from './ui';

/** The words a contractor meets in tenders, in plain language. One list serves the Help panel and the hover explanations (`Term`). */
export const GLOSSARY: { term: string; meaning: string }[] = [
  { term: 'Tender ID', meaning: tr('The number the portal gives a tender. You need it to find the tender again on the portal.') },
  { term: 'Civil tender', meaning: tr('A tender for construction or engineering work: roads, buildings, drains, bridges, water supply. TendAI keeps these and leaves out supplies and services.') },
  { term: 'NIT', meaning: tr('Notice Inviting Tender: the official notice that describes the work, who may bid, the dates and the conditions.') },
  { term: 'Estimate', meaning: tr('The department\'s own cost for the work. Quotes are often compared with it, for example "5% below the estimate".') },
  { term: 'EMD', meaning: tr('Earnest Money Deposit: money paid with a bid to show you are serious. It is normally returned after the result to bidders who do not win; the tender document gives the exact rules.') },
  { term: 'Tender fee', meaning: tr('A fee for the tender documents or the portal. It is not refunded.') },
  { term: 'BOQ', meaning: tr('Bill of Quantities: the list of work items with their quantities. You fill in your rate for each item and the total becomes your price.') },
  { term: 'L1', meaning: tr('The lowest bidder, who usually gets the work if the bid is otherwise in order.') },
  { term: 'Technical and price bid', meaning: tr('Most tenders have two parts. The technical bid shows you qualify; the price bid is opened only for those who do.') },
  { term: 'Corrigendum', meaning: tr('An official change to a tender after it was published, such as a new closing date. Check for them before you submit.') },
  { term: 'Pre-bid meeting', meaning: tr('A meeting where bidders can ask the department questions about the tender.') },
  { term: 'Bank guarantee', meaning: tr('A bank\'s written promise to pay if you fail to meet your obligations. Sometimes accepted instead of cash.') },
  { term: 'Vault', meaning: tr('Where TendAI keeps your standard papers (GST, PAN, registration, balance sheets) so each bid\'s checklist can match them.') },
  { term: 'Fit score', meaning: tr('How well a tender matches your company, from 1 to 5, worked out from your company description and the tender\'s work.') },
];

/** A word with its plain meaning on hover (and in the Help panel). */
export function Term({ k, children }: { k: string; children?: string }) {
  const meaning = GLOSSARY.find((g) => g.term === k)?.meaning;
  return <abbr className="term" title={meaning}>{children ?? k}</abbr>;
}

const STEPS: { title: string; text: string; href: string; where: string }[] = [
  { title: tr('Tell TendAI about your company'), text: tr('A few lines about the work you do and your registration class. It uses them to say which tenders suit you.'), href: '#/vault', where: tr('Vault > Company') },
  { title: tr('Collect tenders'), text: tr('Press Collect tenders now on the Dashboard. The first run takes 20 to 40 minutes; after that it runs by itself every day.'), href: '#/dashboard', where: tr('Dashboard') },
  { title: tr('Find the right ones'), text: tr('Search in plain words ("road nashik", "drain above 2 crore"), filter by place and size, and star the ones you like. The order learns from your stars.'), href: '#/tenders', where: tr('Tenders') },
  { title: tr('Start a bid'), text: tr('Open a tender and press Start a bid. You get a checklist of papers, price tools, letters and deadline reminders in one place.'), href: '#/bids', where: tr('Bids') },
  { title: tr('Record what happened'), text: tr('Mark the bid Won or Lost with the prices. Over time TendAI learns what wins for you.'), href: '#/bids', where: tr('Bids') },
];

const WHERE: [string, string, string][] = [
  [tr('Tenders closing soon'), '#/dashboard', tr('Dashboard')],
  [tr('My bids and their deadlines'), '#/bids', tr('Bids')],
  [tr('Money locked up in EMDs'), '#/dashboard', tr('Dashboard')],
  [tr('Who gets the daily email'), '#/settings', tr('Settings > Daily email')],
  [tr('A tender TendAI could not collect'), '#/tenders', tr('Tenders > Add a tender')],
];

/** A short guide for someone new: what to do first, where things are, and what the words mean. Opens from the menu, or from anywhere with the "open-help" event. */
export function HelpDrawer({ onClose }: { onClose: () => void }) {
  return (
    <Drawer title={tr('Help')} onClose={onClose} wide>
      <h3 style={{ margin: '0 0 6px' }}>{tr('Getting started')}</h3>
      <ol className="help-steps">
        {STEPS.map((s) => (
          <li key={s.title}>
            <div><strong>{s.title}</strong><div className="muted small">{s.text}</div></div>
            <a className="btn small" href={s.href} onClick={onClose}>{s.where}</a>
          </li>
        ))}
      </ol>
      <h3 style={{ margin: '18px 0 6px' }}>{tr('Where do I find…')}</h3>
      <ul className="help-where">
        {WHERE.map(([what, href, where]) => <li key={what}>{what}: <a href={href} onClick={onClose}>{where}</a></li>)}
      </ul>
      <h3 style={{ margin: '18px 0 6px' }}>{tr('Words you will see')}</h3>
      <dl className="facts help-glossary">
        {GLOSSARY.map((g) => <div key={g.term} style={{ display: 'contents' }}><dt>{g.term}</dt><dd>{g.meaning}</dd></div>)}
      </dl>
      <p className="muted small" style={{ marginTop: 14 }}>{tr('Press ? any time for the keyboard shortcuts.')} <button type="button" className="link-btn" onClick={() => { onClose(); window.dispatchEvent(new Event('open-tour')); }}>{tr('Take the tour again')}</button></p>
    </Drawer>
  );
}

/** Keeps the Help panel's open state, and lets other parts of the page ask for it. */
export function useHelp(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener('open-help', show);
    return () => window.removeEventListener('open-help', show);
  }, []);
  return [open, setOpen];
}
