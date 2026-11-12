import assert from 'node:assert/strict';
import test from 'node:test';

import dayjs from 'dayjs';

import { experienceDateBounds } from '../src/modules/file-browser/experience-date-range.ts';

// 断言 UTC 日期，与时区无关：边界本身就是 UTC 语义，用本地格式化会在非 UTC
// 时区下读成前一天。
//
// Assert the UTC date, independent of the machine's zone: the bounds are UTC by
// contract, and local formatting reads as the previous day outside UTC.
const show = (bound) => bound.toDate().toISOString().slice(0, 10);

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

test('the subtraction stays on UTC days across a DST transition', () => {
  // 跨夏令时切换时本地日历一天只有 23 或 25 小时；按本地日历减法会落到前一天
  // 23:30，按 UTC 格式化后 start_date 就早了一天。
  //
  // Across a DST transition a local calendar day holds 23 or 25 hours; subtracting in
  // local time lands at 23:30 the day before and formats — in UTC — one day early.
  const now = dayjs('2026-11-03T00:30:00Z');
  const bounds = experienceDateBounds(7, now);
  assert.ok(bounds);
  // 断言的是 UTC 日期，与时区无关。
  assert.equal(show(bounds.start), '2026-10-28');
  assert.equal(show(bounds.end), '2026-11-03');
});
