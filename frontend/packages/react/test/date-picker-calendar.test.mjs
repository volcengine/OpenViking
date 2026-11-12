import assert from 'node:assert/strict';
import test from 'node:test';

import dayjs from 'dayjs';

import { toApiDate } from '../src/shared/components/date-picker/api-date.ts';
import {
  buildCalendar,
  monthTitle,
  parseDate,
  shiftMonth,
  weekdayNames,
} from '../src/shared/components/date-picker/calendar.ts';

test('a valid YYYY-MM-DD parses to local midnight', () => {
  const date = parseDate('2026-11-12');
  assert.ok(date);
  assert.equal(date.year(), 2026);
  assert.equal(date.month(), 10);
  assert.equal(date.date(), 12);
  assert.equal(date.hour(), 0);
});

test('dates that would roll over are rejected rather than shifted', () => {
  // `Date` 会把这三个分别进位到 3 月 2 日、次年 1 月、3 月 1 日；筛选里出现这种
  // 输入应当判为无效，而不是悄悄换成另一个日子。
  assert.equal(parseDate('2026-02-31'), null);
  assert.equal(parseDate('2026-13-01'), null);
  assert.equal(parseDate('2025-02-29'), null);
});

test('a leap day is accepted only in a leap year', () => {
  assert.ok(parseDate('2024-02-29'));
  assert.equal(parseDate('2026-02-29'), null);
});

test('an empty or malformed value parses to null', () => {
  for (const value of ['', '   ', 'abc', '2026-1-2', '2026/11/12', '20261112'])
    assert.equal(parseDate(value), null, `expected null for ${value}`);
});

test('formatting round-trips through parseDate', () => {
  for (const value of ['2026-01-01', '2026-11-12', '2024-12-31'])
    assert.equal(toApiDate(parseDate(value)), value);
});

test('the grid is six rows of seven and starts on Monday', () => {
  const cells = buildCalendar(2026, 10);
  assert.equal(cells.length, 42);
  // 2026-11-01 是周日，网格从周一开始，所以首格是 10 月 26 日。
  assert.equal(toApiDate(cells[0].date), '2026-10-26');
});

test('weekday headings come from Intl and start on Monday', () => {
  const zh = weekdayNames('zh-CN');
  assert.equal(zh.length, 7);
  // 首列是周一，末列是周日——与网格的起点一致。
  assert.match(zh[0], /一/);
  assert.match(zh[6], /日/);

  const en = weekdayNames('en-US');
  assert.equal(en.length, 7);
  assert.match(en[0], /Mon/);
  assert.match(en[6], /Sun/);
});

test('the month heading follows the given locale', () => {
  assert.match(monthTitle('zh-CN', 2026, 10), /2026/);
  assert.match(monthTitle('zh-CN', 2026, 10), /11/);
  assert.match(monthTitle('en-US', 2026, 10), /November/);
  assert.match(monthTitle('en-US', 2026, 10), /2026/);
});

test('every day of the month appears exactly once and outside days are marked', () => {
  const cells = buildCalendar(2026, 10);
  const inMonth = cells.filter((cell) => cell.inMonth);
  assert.equal(inMonth.length, 30);
  assert.deepEqual(
    inMonth.map((cell) => cell.date.date()),
    Array.from({ length: 30 }, (_, index) => index + 1),
  );
  assert.equal(cells.filter((cell) => !cell.inMonth).length, 12);
});

test('today is flagged only on the matching cell', () => {
  const cells = buildCalendar(2026, 10, dayjs('2026-11-12'));
  const today = cells.filter((cell) => cell.today);
  assert.equal(today.length, 1);
  assert.equal(toApiDate(today[0].date), '2026-11-12');
});

test('shifting the month crosses year boundaries in both directions', () => {
  assert.deepEqual(shiftMonth(2026, 0, -1), { year: 2025, month: 11 });
  assert.deepEqual(shiftMonth(2026, 11, 1), { year: 2027, month: 0 });
  assert.deepEqual(shiftMonth(2026, 5, 0), { year: 2026, month: 5 });
});
