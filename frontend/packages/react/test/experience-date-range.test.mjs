import assert from 'node:assert/strict';
import test from 'node:test';

import dayjs from 'dayjs';

import { experienceDateBounds } from '../src/modules/file-browser/experience-date-range.ts';

const show = (bound) => bound.format('YYYY-MM-DD');

test('the bounds span exactly `days` days, ending on the given instant', () => {
  const bounds = experienceDateBounds(7, dayjs('2026-11-12T12:00:00Z'));
  assert.ok(bounds);
  assert.equal(show(bounds.end), '2026-11-12');
  assert.equal(show(bounds.start), '2026-11-06');
});

test('the bounds cross a month and a year boundary', () => {
  const bounds = experienceDateBounds(5, dayjs('2027-01-02T00:00:00Z'));
  assert.ok(bounds);
  assert.equal(show(bounds.start), '2026-12-29');
  assert.equal(show(bounds.end), '2027-01-02');
});

test('a single day yields the same instant at both ends', () => {
  const now = dayjs('2026-11-12T00:00:00Z');
  const bounds = experienceDateBounds(1, now);
  assert.ok(bounds);
  assert.equal(show(bounds.start), '2026-11-12');
  assert.equal(show(bounds.end), '2026-11-12');
});

test('a zero or negative day count asks for no bound at all', () => {
  // 负数会算出一个起点晚于终点的区间，服务端直接拒绝，因此与 0 一样返回 null。
  assert.equal(experienceDateBounds(0), null);
  assert.equal(experienceDateBounds(-1), null);
});
