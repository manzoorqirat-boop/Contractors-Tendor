import { describe, expect, it } from 'vitest';
import { fieldProblems, firstField } from './api';

describe('fieldProblems', () => {
  it('names the field and what is wrong with it', () => {
    expect(fieldProblems([{ loc: ['body', 'text'], msg: 'Field required' }])).toBe('Please check: text: Field required');
    expect(fieldProblems([{ loc: ['query', 'page_size'], msg: 'Input should be less than or equal to 100' }])).toBe('Please check: page size: Input should be less than or equal to 100');
  });
  it('uses the innermost named part of a nested field and drops the "Value error" prefix', () => {
    expect(fieldProblems([{ loc: ['body', 'views', 0, 'name'], msg: 'Value error, Give it a name' }])).toBe('Please check: name: Give it a name');
  });
  it('lists the first three and counts the rest', () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ loc: ['body', `f${i}`], msg: 'bad' }));
    expect(fieldProblems(many)).toBe('Please check: f0: bad; f1: bad; f2: bad; and 2 more');
  });
  it('copes with a problem it cannot read', () => {
    expect(fieldProblems([null, {}, { loc: [], msg: 7 }])).toBe('Please check: is not valid; is not valid; is not valid');
  });
});

describe('firstField', () => {
  it('names the box the first problem is about', () => {
    expect(firstField([{ loc: ['body', 'text'], msg: 'x' }, { loc: ['body', 'role'], msg: 'y' }])).toBe('text');
    expect(firstField([{ loc: ['query', 'page_size'], msg: 'x' }])).toBe('page_size');
    expect(firstField([{ loc: ['body', 'views', 0, 'name'], msg: 'x' }])).toBe('name');
  });
  it('is empty when there is nothing to name', () => {
    expect(firstField([])).toBe('');
    expect(firstField([null])).toBe('');
    expect(firstField([{ loc: [], msg: 'x' }])).toBe('');
    expect(firstField([{ loc: 'body', msg: 'x' }])).toBe('');
  });
});
