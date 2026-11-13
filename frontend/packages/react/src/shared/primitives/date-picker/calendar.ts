/**
 * 日历网格的计算：给定年月，排出 6×7 的日期格。日期算术与解析走 dayjs；给人看的
 * 月份名、周名走 `Intl`；发给接口的日期串走 `toApiDate`。不碰 DOM。
 *
 * Calendar-grid maths: given a year and month, lay out a 6×7 grid of days. Arithmetic
 * and parsing go through dayjs; human-facing month and weekday names go through `Intl`;
 * dates sent to an endpoint go through `toApiDate`. The DOM is never touched.
 */
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';

/** 一格里的一天。One cell of the grid. */
export interface CalendarCell {
  /** 该格的日期。The cell's date. */
  date: Dayjs;
  /** 是否属于当前月；前后补位为 `false`。Whether it belongs to the shown month. */
  inMonth: boolean;
  /** 是否今天。Whether it is today. */
  today: boolean;
}

/** 一周从周几开始：0 = 周日。Which weekday starts the week; 0 = Sunday. */
export const WEEK_START = 1;

/**
 * 周标题与月份标题走 `Intl`，跟随传入的语言。`Intl` 的 polyfill 由宿主提供，库
 * 不打包也不声明。
 *
 * Weekday and month headings go through `Intl` and follow the locale passed in. The host
 * provides `Intl`'s polyfills; the library neither bundles nor declares them.
 */

/** 从周一开始的七个短周名。Seven short weekday names, starting Monday. */
export function weekdayNames(locale: string): string[] {
  const format = new Intl.DateTimeFormat(locale, { weekday: 'short' });
  // 2024-01-01 是周一，用它的后七天依次取名字。
  //
  // 2024-01-01 is a Monday; read the following seven days off it.
  return Array.from({ length: 7 }, (_, index) =>
    format.format(dayjs('2024-01-01').add(index, 'day').toDate()),
  );
}

/** 某年某月的标题，例如「2026年11月」。The heading of one month, e.g. "November 2026". */
export function monthTitle(
  locale: string,
  year: number,
  month: number,
): string {
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: 'long',
  }).format(dayjs().year(year).month(month).date(1).toDate());
}

/**
 * 排出某个月的 6×7 网格。固定六行，月份切换时高度不变，弹层不会跳。
 *
 * Lays out a 6×7 grid for one month. Six rows are always emitted so the popup keeps its
 * height when the month changes instead of jumping.
 */
export function buildCalendar(
  year: number,
  month: number,
  today: Dayjs = dayjs(),
): CalendarCell[] {
  // 每一格都从当天零点构造，且必须从**同一个**基准日出发。以 `dayjs()` 为基准
  // 会把当前时分秒带进每一格，而选中值经 `startOf('day')` 归零，两边永远差几小时
  // ——已选日期重新打开时一格都不高亮。`startOf('day')` 只保留日期部分，也让基准
  // 日与「今天」的比较不受时刻影响。
  //
  // Every cell is built at midnight from one and the same base day. Starting from
  // `dayjs()` would carry the current time into each cell while the selected value is
  // zeroed by `startOf('day')`, so the two never match and a reopened picker highlights
  // nothing. `startOf('day')` keeps only the date part and also keeps the base day's
  // comparison with "today" free of the clock.
  const base = today.startOf('day');
  const first = base.year(year).month(month).date(1);
  const offset = (first.day() - WEEK_START + 7) % 7;
  const start = first.subtract(offset, 'day');
  const cells: CalendarCell[] = [];
  for (let index = 0; index < 42; index++) {
    const date = start.add(index, 'day');
    cells.push({
      date,
      inMonth: date.month() === month,
      today: date.isSame(base, 'day'),
    });
  }
  return cells;
}

/**
 * 解析 `YYYY-MM-DD`。空串或形状不符时返回 `null`——日期筛选里「没填」是合法
 * 状态，不能当成错误。
 *
 * 判据是「格式化回去是否等于输入」：dayjs 与 `Date` 一样会把 `2026-02-31` 进位成
 * 3 月 3 日，只有把解析结果再格式化一次与原文比对，才能同时挡住非法日子、越界月份
 * 和没补零的写法（`2026-2-3`），且不需要额外插件。
 *
 * Parses `YYYY-MM-DD`, returning `null` for an empty or malformed value: "not filled"
 * is a valid state in a date filter and must not read as an error.
 *
 * The test is whether formatting the parsed value reproduces the input: dayjs rolls
 * 2026-02-31 into March 3 just as `Date` does, and only comparing the round trip catches
 * an impossible day, an out-of-range month and an unpadded `2026-2-3` at once, with no
 * extra plugin.
 */
export function parseDate(value: string): Dayjs | null {
  const trimmed = value.trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return null;
  const date = dayjs(trimmed);
  return date.isValid() && date.format('YYYY-MM-DD') === trimmed ? date : null;
}

/** 某月的上一个月或下一个月。The month before or after the given one. */
export function shiftMonth(
  year: number,
  month: number,
  delta: number,
): { year: number; month: number } {
  const shifted = dayjs().year(year).month(month).date(1).add(delta, 'month');
  return { year: shifted.year(), month: shifted.month() };
}

/** 当天零点，用于与格子比较。Today at midnight, for comparing against a cell. */
export const startOfDay = (date: Dayjs): Dayjs => date.startOf('day');
