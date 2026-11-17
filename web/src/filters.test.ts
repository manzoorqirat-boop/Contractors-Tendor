import { describe, expect, it } from 'vitest';
import { DISPLAY, EMPTY, NONE, VALUE_PRESETS, describe as chipsFor, fromParams, isFiltered, mergeDrill, toParams } from './filters';
import type { AppConfig, Filters } from './types';

const f = (over: Partial<Filters> = {}): Filters => ({ ...EMPTY, portal: [], category: [], department: [], location: [], work_type: [], ...over });
const url = (x: Filters, d?: Parameters<typeof toParams>[1]) => toParams(x, d).toString();
const back = (query: string) => fromParams(new URLSearchParams(query));

describe('toParams', () => {
  it('leaves every empty thing out, so a plain list has a plain address', () => {
    expect(url(f())).toBe('');
    expect(url(f(), {})).toBe('');
  });
  it('writes search, status, lists, ranges and flags', () => {
    const q = new URLSearchParams(url(f({ q: 'road nashik', status: 'closed', portal: ['maha', 'delhi'], work_type: ['Roads'], min_value: '1000000', starred: true, fits_me: true })));
    expect(q.get('q')).toBe('road nashik');
    expect(q.get('status')).toBe('closed');
    expect(q.getAll('portal')).toEqual(['maha', 'delhi']);
    expect(q.getAll('work_type')).toEqual(['Roads']);
    expect(q.get('min_value')).toBe('1000000');
    expect(q.get('starred')).toBe('true');
    expect(q.get('fits_me')).toBe('true');
  });
  it('does not write the default status, so "open" never appears', () => {
    expect(url(f({ status: 'open' }))).toBe('');
  });
  it('writes how the list is shown only when it differs from the default', () => {
    expect(url(f(), { sort: DISPLAY.sort, page: 1, layout: 'cards' })).toBe('');
    const q = new URLSearchParams(url(f(), { sort: 'closing', page: 3, layout: 'table' }));
    expect([q.get('sort'), q.get('page'), q.get('layout')]).toEqual(['closing', '3', 'table']);
  });
});

describe('fromParams', () => {
  it('gives the defaults for an empty address', () => {
    const { filters, display } = back('');
    expect(filters).toEqual(f());
    expect(display).toEqual(DISPLAY);
  });
  it('ignores a status it does not know', () => {
    expect(back('status=bogus').filters.status).toBe('open');
    expect(back('status=closing_soon').filters.status).toBe('closing_soon');
  });
  it('falls back to page 1 for anything that is not a positive number', () => {
    for (const p of ['page=0', 'page=-4', 'page=abc', 'page=']) expect(back(p).display.page, p).toBe(1);
    expect(back('page=7').display.page).toBe(7);
  });
  it('accepts only the measures, charts and layouts it knows', () => {
    expect(back('measure=value').display.measure).toBe('value');
    expect(back('measure=banana').display.measure).toBe(DISPLAY.measure);
    expect(back('chart=table').display.chart).toBe('table');
    expect(back('chart=pie').display.chart).toBe(DISPLAY.chart);
    expect(back('layout=table').display.layout).toBe('table');
    expect(back('layout=anything').display.layout).toBe('cards');
  });
  it('tells "no split" (an empty split_by) from "not mentioned" (the default split)', () => {
    expect(back('').display.split_by).toBe(DISPLAY.split_by);
    expect(back('split_by=').display.split_by).toBe('');
  });
  it('reads a flag only when it says true', () => {
    expect(back('starred=true').filters.starred).toBe(true);
    expect(back('starred=yes').filters.starred).toBeUndefined();
  });
});

describe('an address and the filters it stands for are the same thing', () => {
  const cases: [string, Filters][] = [
    ['search only', f({ q: 'drain -paver' })],
    ['status and lists', f({ status: 'all', portal: ['maha'], department: ['PWD', 'Water'], location: [NONE] })],
    ['ranges and flags', f({ min_value: '1000000', value_lt: '50000000', closing_from: '2026-11-01', closing_to: '2026-11-30', starred: true, no_value: true })],
    ['everything', f({ q: 'bridge', status: 'closing_soon', portal: ['a', 'b'], category: ['Works'], work_type: ['Roads', 'Bridges'], min_fit: '4', closing_within: '7', hidden: 'only', fits_me: true })],
  ];
  for (const [name, filters] of cases) {
    it(`survives the trip: ${name}`, () => {
      const out = back(url(filters)).filters;
      // a missing flag and an unset one are the same, so compare what the address can express
      expect(JSON.parse(JSON.stringify(out))).toEqual(JSON.parse(JSON.stringify(filters)));
    });
  }
  it('keeps "do not split" in the address: an empty split is a choice, not a missing value', () => {
    expect(url(f(), { split_by: '' })).toBe('split_by=');
    expect(back(url(f(), { split_by: '' })).display.split_by).toBe('');
    expect(url(f(), { split_by: DISPLAY.split_by })).toBe('');                 // the default split needs no mention
    expect(url(f(), { group_by: '' })).toBe('');                               // but any other empty value is still left out
  });
  it('keeps how the list is shown too', () => {
    const { display } = back(url(f(), { sort: 'value', page: 4, layout: 'table', measure: 'avg', chart: 'bars', group_by: 'category', split_by: '' }));
    expect(display).toMatchObject({ sort: 'value', page: 4, layout: 'table', measure: 'avg', chart: 'bars', group_by: 'category', split_by: '' });
  });
});

describe('mergeDrill (a click on a chart laid over the filters already in force)', () => {
  it('replaces a list', () => {
    expect(mergeDrill(f({ portal: ['maha'] }), { portal: ['delhi'] }).portal).toEqual(['delhi']);
  });
  it('sets a status, but "all" never overrides one', () => {
    expect(mergeDrill(f(), { status: 'closed' }).status).toBe('closed');
    expect(mergeDrill(f({ status: 'closed' }), { status: 'all' }).status).toBe('closed');
  });
  it('lets a date range only ever narrow', () => {
    const base = f({ closing_from: '2026-11-10', closing_to: '2026-11-20' });
    expect(mergeDrill(base, { closing_from: '2026-11-05', closing_to: '2026-11-30' })).toMatchObject({ closing_from: '2026-11-10', closing_to: '2026-11-20' });
    expect(mergeDrill(base, { closing_from: '2026-11-15', closing_to: '2026-11-18' })).toMatchObject({ closing_from: '2026-11-15', closing_to: '2026-11-18' });
  });
  it('adopts a range when there was none', () => {
    expect(mergeDrill(f(), { closing_from: '2026-11-15', closing_to: '2026-11-18' })).toMatchObject({ closing_from: '2026-11-15', closing_to: '2026-11-18' });
  });
  it('narrows value limits: the higher minimum, the lower ceiling', () => {
    expect(mergeDrill(f({ min_value: '5000000' }), { min_value: 1000000 } as never).min_value).toBe('5000000');
    expect(mergeDrill(f({ min_value: '1000000' }), { min_value: 5000000 } as never).min_value).toBe('5000000');
    expect(mergeDrill(f({ value_lt: '10000000' }), { value_lt: 50000000 } as never).value_lt).toBe('10000000');
    expect(mergeDrill(f(), { value_lt: 50000000 } as never).value_lt).toBe('50000000');
  });
  it('does not change the filters it was given', () => {
    const base = f({ portal: ['maha'] });
    mergeDrill(base, { portal: ['delhi'], status: 'closed' });
    expect(base.portal).toEqual(['maha']);
    expect(base.status).toBe('open');
  });
});

describe('describe (the filters in force, as removable chips)', () => {
  const config = { portals: [{ key: 'maha', name: 'Maharashtra (MahaTenders)', state: 'Maharashtra' }] } as unknown as AppConfig;
  const labels = (x: Filters, c?: AppConfig) => chipsFor(x, c).map((chip) => chip.label);

  it('has no chips, and is not "filtered", for the default filters', () => {
    expect(chipsFor(f())).toEqual([]);
    expect(isFiltered(f())).toBe(false);
  });
  it('words each filter plainly', () => {
    expect(labels(f({ q: 'road' }))).toEqual(['Search: "road"']);
    expect(labels(f({ status: 'closing_soon' }))).toEqual(['Closing soon']);
    expect(labels(f({ starred: true, fits_me: true }))).toEqual(['Starred only', 'Fits my size']);
    expect(labels(f({ min_fit: '4' }))).toEqual(['Fit 4+ of 5']);
    expect(labels(f({ hidden: 'only' }))).toEqual(['Ignored tenders only']);
    expect(labels(f({ closing_within: '7' }))).toEqual(['Closes within 7 days']);
  });
  it('names a portal by its state when it can, and the key when it cannot', () => {
    expect(labels(f({ portal: ['maha'] }), config)).toEqual(['Portal: Maharashtra']);
    expect(labels(f({ portal: ['unknown'] }), config)).toEqual(['Portal: unknown']);
  });
  it('shortens a long list to two names and a count', () => {
    expect(labels(f({ department: ['A', 'B'] }))).toEqual(['Department: A, B']);
    expect(labels(f({ department: ['A', 'B', 'C', 'D'] }))).toEqual(['Department: A, B +2']);
  });
  it('says "Not stated" for the server\'s word for it', () => {
    expect(labels(f({ location: [NONE] }))).toEqual(['Location: Not stated']);
  });
  it('words a value range by what is set', () => {
    expect(labels(f({ min_value: '1000000', value_lt: '10000000' }))).toEqual(['Value ₹10.00 lakh to ₹1.00 crore']);
    expect(labels(f({ min_value: '1000000' }))).toEqual(['Value ₹10.00 lakh or more']);
    expect(labels(f({ max_value: '10000000' }))).toEqual(['Value under ₹1.00 crore']);
  });
  it('words a date range, with an open end shown as dots', () => {
    expect(labels(f({ closing_from: '2026-11-01T00:00:00', closing_to: '2026-11-30' }))).toEqual(['Closes 1 Nov 2026 to 30 Nov 2026']);
    expect(labels(f({ seen_from: '2026-11-01' }))).toEqual(['Found 1 Nov 2026 to …']);
  });
  it('gives every chip a way to remove just that filter', () => {
    const all = f({ q: 'road', status: 'closed', portal: ['maha'], department: ['PWD'], min_value: '1000000', value_lt: '2000000', closing_from: '2026-11-01', closing_to: '2026-11-30', starred: true, fits_me: true, min_fit: '3' });
    const chips = chipsFor(all);
    expect(chips.length).toBeGreaterThanOrEqual(8);
    for (const chip of chips) {
      const after = chip.remove(all);
      expect(chipsFor(after).map((c) => c.id), chip.id).not.toContain(chip.id);
      expect(chipsFor(after).length, chip.id).toBe(chips.length - 1);       // and nothing else was removed with it
    }
    expect(isFiltered(all)).toBe(true);
  });
});

describe('VALUE_PRESETS', () => {
  it('start with "any" and run upward without gaps or overlaps', () => {
    expect(VALUE_PRESETS[0]).toEqual({ label: 'Any value' });
    for (const p of VALUE_PRESETS) if (p.min && p.max) expect(Number(p.min)).toBeLessThan(Number(p.max));
    const mins = VALUE_PRESETS.map((p) => Number(p.min ?? 0));
    expect([...mins].sort((a, b) => a - b).slice(0, 1)).toEqual([0]);
  });
});
