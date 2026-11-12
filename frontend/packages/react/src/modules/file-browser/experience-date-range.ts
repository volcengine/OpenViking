/**
 * 经验看板的时间区间。单独成文件是为了能直接跑源码测试：`experience-data.ts` 在
 * 运行时导入 `client/paths`，测试加载它时会因为 `.js` 后缀解析不到而失败。
 *
 * 这里只做区间算术，日期串交给 `toApiDate(..., 'utc')` 生成；两处都要能单独测，
 * 因此本文件不引用它，避免把 `.js` 后缀的运行时导入带进测试。
 *
 * The time range of the experience dashboard. It lives in its own file so a test can run
 * it straight from source: `experience-data.ts` imports `client/paths` at runtime, and a
 * test loading that file fails to resolve the `.js` suffix.
 *
 * Only the range arithmetic lives here; the date strings come from
 * `toApiDate(..., 'utc')`. Both are tested on their own, so this file does not import it —
 * doing so would drag a `.js` runtime import into the test.
 */
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';

/**
 * 区间两端的日期。调用方用 `toApiDate(..., 'utc')` 转成接口串；这里返回 Dayjs 是
 * 为了让「取哪一天」的决定留在唯一的地方。
 *
 * The two ends of the range. The caller turns them into API strings with
 * `toApiDate(..., 'utc')`; returning Dayjs keeps the "which day counts" decision in one
 * place.
 */
export interface ExperienceDateBounds {
  start: Dayjs;
  end: Dayjs;
}

/**
 * 近 N 天的日期区间，供 `agent_evolution` 列表接口使用。
 *
 * 非正数表示「不限时间」，返回 `null`：负数会算出一个起点晚于终点的区间，服务端
 * 直接拒绝（`start_date must be earlier than or equal to end_date`）。
 *
 * The date range of the last N days for the `agent_evolution` list endpoints.
 *
 * A non-positive count means "no time bound" and returns `null`: a negative one produces
 * a range whose start is after its end, which the server rejects outright.
 */
export function experienceDateBounds(
  days: number,
  now = dayjs(),
): ExperienceDateBounds | null {
  if (days <= 0) return null;
  // 减法必须在 UTC 上做。`subtract(..., 'day')` 走本地日历，跨夏令时切换时那
  // 一天只有 23 或 25 小时，结果会偏到前一天的 23:30 之类，按 UTC 格式化后
  // `start_date` 就早了一天（纽约时区跨 2026-11-01 DST 结束时实测 10-27 vs
  // 期望 10-28）。接口的边界是 UTC，这里也按 UTC 算。
  //
  // The subtraction happens in UTC. `subtract(..., 'day')` walks the local calendar, and
  // a DST transition day holds 23 or 25 hours, so the result can land at 23:30 the day
  // before and format — in UTC — one day early (measured in America/New_York across the
  // 2026-11-01 DST end: 10-27 instead of 10-28). The endpoint's bounds are UTC, so the
  // arithmetic is too.
  const end = now.toDate();
  const start = new Date(end.getTime());
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return { start: dayjs(start), end: dayjs(end) };
}
