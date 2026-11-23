import { tr } from './i18n';
import type { Clause, DocFacts, Evidence } from './types';

/** How far a quoted piece of evidence can be trusted: found word for word in the document, not found, or nothing to check against. */
export function EvidenceBadge({ verified }: { verified: boolean | null }) {
  if (verified === true) return <span className="badge ok">{tr('Found in the document')}</span>;
  if (verified === false) return <span className="badge warn">{tr('Not found word for word: check the document')}</span>;
  return <span className="badge plain">{tr('Not checked (no text to compare)')}</span>;
}

/** The conditions the fixed rules found, each with the sentence it came from. No AI involved. */
export function ClauseList({ clauses }: { clauses: Clause[] }) {
  if (clauses.length === 0) return null;
  return (
    <dl className="facts">
      {clauses.map((c) => (
        <div key={c.kind} style={{ display: 'contents' }}>
          <dt>{c.label}</dt>
          <dd>
            <strong>{c.value}</strong>
            {c.watch && <p className="clause-watch" role="note">{c.watch}</p>}
            <blockquote className="quote">“{c.quote}”</blockquote>
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** The AI's reading with the words it quoted for each point, so each can be checked in a second. */
export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) return null;
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {evidence.map((e, i) => (
        <li key={i} style={{ marginBottom: 8 }}>
          <div><strong>{e.item}</strong> <EvidenceBadge verified={e.verified} /></div>
          <blockquote className="quote">“{e.quote}”</blockquote>
        </li>
      ))}
    </ul>
  );
}

/** What was read from a bid's tender text: the rules' clauses, and the AI's claims with their quotes when it was used. */
export function DocumentEvidence({ doc, found }: { doc: DocFacts | null; found: Clause[] | null }) {
  const clauses = found ?? doc?.clauses ?? [];
  const evidence = doc?.evidence ?? [];
  if (clauses.length === 0 && evidence.length === 0) return null;
  const bad = evidence.filter((e) => e.verified === false).length;
  return (
    <div style={{ marginTop: 12 }}>
      {clauses.length > 0 && (
        <>
          <h3 style={{ margin: '0 0 4px' }}>{tr('Conditions found in the text')}</h3>
          <p className="sub">{tr('Found by fixed rules, each with the sentence it came from. Nothing is guessed: a condition the text does not state is not listed.')}</p>
          <ClauseList clauses={clauses} />
        </>
      )}
      {evidence.length > 0 && (
        <>
          <h3 style={{ margin: '14px 0 4px' }}>{tr('What the AI said, and where')}</h3>
          {bad > 0 && <div className="alert warn" role="status">{bad === 1 ? tr('One quote could not be found in the document. The AI may have misread or paraphrased: check it before relying on it.') : tr('{n} quotes could not be found in the document. The AI may have misread or paraphrased: check them before relying on them.', { n: bad })}</div>}
          <EvidenceList evidence={evidence} />
        </>
      )}
    </div>
  );
}
