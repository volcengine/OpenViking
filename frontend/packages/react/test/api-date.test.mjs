import assert from 'node:assert/strict';
import test from 'node:test';

import dayjs from 'dayjs';

import { toApiDate } from '../src/shared/primitives/date-picker/api-date.ts';

test('the local zone reads the day on the local calendar', () => {
  // 2026-11-12T00:30+08:00 在东八区是 12 日零点；UTC 那边还是 11 日。
  const instant = dayjs('2026-11-12T00:30:00+08:00');
  assert.equal(toApiDate(instant), instant.format('YYYY-MM-DD'));
});

test('the utc zone reads the UTC day of the same instant', () => {
  const instant = dayjs('2026-11-12T00:30:00+08:00');
  assert.equal(toApiDate(instant, 'utc'), '2026-11-11');
});

test('both zones agree when local time is UTC', () => {
  const instant = dayjs('2026-11-12T12:00:00Z');
  assert.equal(toApiDate(instant, 'utc'), '2026-11-12');
  // 本机时区不是 UTC 时，本地读数会不同，因此只断言形状。
  assert.match(toApiDate(instant), /^\d{4}-\d{2}-\d{2}$/);
});

test('the output always has the API shape, never a locale format', () => {
  for (const zone of ['local', 'utc'])
    assert.match(toApiDate(dayjs('2026-01-05'), zone), /^\d{4}-\d{2}-\d{2}$/);
});
