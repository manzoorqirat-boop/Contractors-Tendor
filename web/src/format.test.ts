import { describe, expect, it } from 'vitest';
import { ambiguousDate, chain, dateSay, duration, fmtDate, fmtDay, headline, inr, inrShort, parseMoney, plural, relative } from './format';

// The server speaks in India time without a zone, and the page must show exactly that: no conversion to the viewer's clock.

describe('fmtDate', () => {
  it('shows nothing-known as a dash', () => {
    expect(fmtDate(null)).toBe('-');
    expect(fmtDate(undefined)).toBe('-');
    expect(fmtDate('')).toBe('-');
  });
  it('writes a date as day, month name, year', () => {
    expect(fmtDate('2026-11-14')).toBe('14 Nov 2026');
    expect(fmtDate('2026-01-05')).toBe('5 Jan 2026');
  });
  it('adds a 12-hour time when asked and a time is there', () => {
    expect(fmtDate('2026-11-14T17:30:00', true)).toBe('14 Nov 2026, 5:30 PM');
    expect(fmtDate('2026-11-14T09:05:00', true)).toBe('14 Nov 2026, 9:05 AM');
  });
  it('gets midnight and noon right', () => {
    expect(fmtDate('2026-11-14T00:05:00', true)).toBe('14 Nov 2026, 12:05 AM');
    expect(fmtDate('2026-11-14T12:00:00', true)).toBe('14 Nov 2026, 12:00 PM');
    expect(fmtDate('2026-11-14T23:59:00', true)).toBe('14 Nov 2026, 11:59 PM');
  });
  it('leaves the time out when the value has none, even if asked', () => {
    expect(fmtDate('2026-11-14', true)).toBe('14 Nov 2026');
  });
  it('hands back text it cannot read, unchanged', () => {
    expect(fmtDate('soon')).toBe('soon');
  });
  it('does not depend on the viewer\'s time zone', () => {
    // the browser build has no Node typings, so the environment is reached through globalThis, in this one test
    const env = (globalThis as unknown as { process: { env: Record<string, string | undefined> } }).process.env;
    const before = env.TZ;
    try {
      for (const tz of ['Asia/Kolkata', 'Pacific/Auckland', 'America/Los_Angeles', 'UTC']) {
        env.TZ = tz;
        expect(fmtDate('2026-11-14T23:59:00', true)).toBe('14 Nov 2026, 11:59 PM');
        expect(relative('2026-11-14T12:00:00', '2026-11-14T10:00:00').text).toBe('Closes in 2 h');
      }
    } finally {
      if (before === undefined) delete env.TZ; else env.TZ = before;
    }
  });
});

describe('ambiguousDate and dateSay', () => {
  it('flags a date that could be read with day and month swapped', () => {
    expect(ambiguousDate('2026-11-05')).toBe(true);      // 05-11 could be 5 Nov or 11 May
    expect(ambiguousDate('2026-03-12')).toBe(true);
  });
  it('does not flag one that has only one reading', () => {
    expect(ambiguousDate('2026-11-20')).toBe(false);     // there is no month 20
    expect(ambiguousDate('2026-11-11')).toBe(false);     // swapped it is the same date
    expect(ambiguousDate('nonsense')).toBe(false);
  });
  it('spells the date out only when it could be misread', () => {
    expect(dateSay('2026-11-05')).toBe('5 Nov 2026');
    expect(dateSay('2026-11-05T17:30:00', true)).toBe('5 Nov 2026, 5:30 PM');
    expect(dateSay('2026-11-20')).toBeNull();
    expect(dateSay('')).toBeNull();
  });
});

describe('fmtDay', () => {
  it('writes day and month', () => {
    expect(fmtDay('2026-11-14')).toBe('14 Nov');
    expect(fmtDay('2026-11-14T10:00:00')).toBe('14 Nov');
  });
  it('hands back what it cannot read', () => {
    expect(fmtDay('Other')).toBe('Other');
  });
});

describe('relative', () => {
  const now = '2026-11-14T10:00:00';
  it('says nothing for no date or an unreadable one', () => {
    expect(relative(null, now)).toEqual({ text: '', tone: 'none' });
    expect(relative('later', now)).toEqual({ text: '', tone: 'none' });
  });
  it('counts the last day in minutes and hours, as urgent', () => {
    expect(relative('2026-11-14T10:30:00', now)).toEqual({ text: 'Closes in 30 min', tone: 'urgent' });
    expect(relative('2026-11-14T12:00:00', now)).toEqual({ text: 'Closes in 2 h', tone: 'urgent' });
    expect(relative('2026-11-15T09:30:00', now)).toEqual({ text: 'Closes in 23 h 30 min', tone: 'urgent' });
  });
  it('keeps counting hours through the second day, as soon', () => {
    expect(relative('2026-11-15T10:00:00', now)).toEqual({ text: 'Closes in 24 h', tone: 'soon' });
    expect(relative('2026-11-16T09:59:00', now)).toEqual({ text: 'Closes in 47 h 59 min', tone: 'soon' });
  });
  it('switches to days from two days out, soon until three days and ok after', () => {
    expect(relative('2026-11-16T10:00:00', now)).toEqual({ text: 'Closes in 2 days', tone: 'soon' });
    expect(relative('2026-11-17T10:00:00', now)).toEqual({ text: 'Closes in 3 days', tone: 'soon' });
    expect(relative('2026-11-18T10:00:00', now)).toEqual({ text: 'Closes in 4 days', tone: 'ok' });
    expect(relative('2026-12-14T10:00:00', now)).toEqual({ text: 'Closes in 30 days', tone: 'ok' });
  });
  it('reports a tender that has closed, by whole days', () => {
    expect(relative('2026-11-14T09:00:00', now)).toEqual({ text: 'Closed today', tone: 'closed' });
    expect(relative('2026-11-13T09:00:00', now)).toEqual({ text: 'Closed 1 day ago', tone: 'closed' });
    expect(relative('2026-11-11T09:00:00', now)).toEqual({ text: 'Closed 3 days ago', tone: 'closed' });
  });
  it('is exact to the minute, with no floating-point drift', () => {
    for (let m = 1; m < 120; m++) {
      const at = new Date(Date.UTC(2026, 10, 14, 10, m)).toISOString().slice(0, 19);
      const text = relative(at, now).text;
      const h = Math.floor(m / 60), min = m % 60;
      expect(text).toBe(h === 0 ? `Closes in ${min} min` : min === 0 ? `Closes in ${h} h` : `Closes in ${h} h ${min} min`);
    }
  });
});

describe('parseMoney', () => {
  it('reads plain numbers, with Indian or western commas and the rupee sign', () => {
    expect(parseMoney('500000')).toBe(500000);
    expect(parseMoney('5,00,000')).toBe(500000);
    expect(parseMoney('₹ 5,00,000')).toBe(500000);
    expect(parseMoney('1,234,567')).toBe(1234567);
  });
  it('reads lakh, crore and thousand in their usual spellings', () => {
    expect(parseMoney('50 lakh')).toBe(5_000_000);
    expect(parseMoney('10 lacs')).toBe(1_000_000);
    expect(parseMoney('₹12L')).toBe(1_200_000);
    expect(parseMoney('1.5 cr')).toBe(15_000_000);
    expect(parseMoney('2 crores')).toBe(20_000_000);
    expect(parseMoney('75k')).toBe(75_000);
    expect(parseMoney('5 thousand')).toBe(5_000);
  });
  it('is not case sensitive', () => {
    expect(parseMoney('2 CR')).toBe(20_000_000);
    expect(parseMoney('3 Lakh')).toBe(300_000);
  });
  it('does not let floating point show through', () => {
    expect(parseMoney('1.1 cr')).toBe(11_000_000);
    expect(parseMoney('0.1 lakh')).toBe(10_000);
    expect(parseMoney('2.3 lakh')).toBe(230_000);
  });
  it('says null for what is not an amount', () => {
    for (const bad of ['', 'abc', '1.2.3', '-5', '5 million', 'lakh', '₹']) expect(parseMoney(bad), bad).toBeNull();
  });
  it('ignores spaces everywhere, so digits grouped with spaces join up (and "5 5" is read as 55)', () => {
    expect(parseMoney('5 00 000')).toBe(500_000);
    expect(parseMoney(' 50  lakh ')).toBe(5_000_000);
    expect(parseMoney('5 5')).toBe(55);                 // lenient by design: spaces are stripped before the number is read
  });
});

describe('inr', () => {
  it('says not stated when there is no value', () => {
    expect(inr(null)).toBe('Not stated');
    expect(inr(undefined)).toBe('Not stated');
  });
  it('uses lakh and crore from a lakh up, to two places', () => {
    expect(inr(851184)).toBe('₹8.51 lakh');
    expect(inr(20991250)).toBe('₹2.10 crore');
    expect(inr(100000)).toBe('₹1.00 lakh');
    expect(inr(10000000)).toBe('₹1.00 crore');
  });
  it('writes smaller amounts in full with Indian grouping', () => {
    expect(inr(99999)).toBe('₹99,999');
    expect(inr(0)).toBe('₹0');
  });
  it('writes everything in full when compact is off', () => {
    expect(inr(851184, false)).toBe('₹8,51,184');
    expect(inr(20991250, false)).toBe('₹2,09,91,250');
  });
});

describe('inrShort', () => {
  it('shortens for chart axes', () => {
    expect(inrShort(12_500_000)).toBe('1.25 Cr');
    expect(inrShort(10_000_000)).toBe('1 Cr');
    expect(inrShort(250_000)).toBe('2.5 L');
    expect(inrShort(12_500)).toBe('12.5k');
    expect(inrShort(999)).toBe('999');
    expect(inrShort(0)).toBe('0');
  });
});

describe('duration', () => {
  it('says running while there is no end', () => {
    expect(duration('2026-11-14T10:00:00', null)).toBe('running');
  });
  it('writes seconds, then minutes and seconds', () => {
    expect(duration('2026-11-14T10:00:00', '2026-11-14T10:00:59')).toBe('59s');
    expect(duration('2026-11-14T10:00:00', '2026-11-14T10:01:01')).toBe('1m 1s');
    expect(duration('2026-11-14T10:00:00', '2026-11-14T10:12:00')).toBe('12m 0s');
  });
  it('never goes negative', () => {
    expect(duration('2026-11-14T10:00:00', '2026-11-14T09:00:00')).toBe('0s');
  });
});

describe('small helpers', () => {
  it('headline prefers the description, then the title', () => {
    expect(headline({ description: 'Widening of road', title: 'Tender 5' })).toBe('Widening of road');
    expect(headline({ description: '', title: 'Tender 5' })).toBe('Tender 5');
  });
  it('chain splits a department path and drops empty parts', () => {
    expect(chain('PWD||Nashik Circle||Nashik Division')).toEqual(['PWD', 'Nashik Circle', 'Nashik Division']);
    expect(chain('PWD||')).toEqual(['PWD']);
    expect(chain('')).toEqual([]);
  });
  it('plural counts with Indian grouping', () => {
    expect(plural(1, 'tender')).toBe('1 tender');
    expect(plural(0, 'tender')).toBe('0 tenders');
    expect(plural(1234567, 'tender')).toBe('12,34,567 tenders');
    expect(plural(2, 'person', 'people')).toBe('2 people');
  });
});
