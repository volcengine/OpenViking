/**
 * 服务端契约的日期串。单独成文件、只依赖 dayjs：`calendar.ts` 在别处会被直接跑源码
 * 的测试加载，而这里也要被模块层复用，放在一起会让模块层反过来依赖日历组件。
 *
 * The date string the API contract expects. It lives in its own file and depends only on
 * dayjs: `calendar.ts` is loaded straight from source by tests elsewhere, and this helper
 * is reused by the module layer, so keeping them together would make a module depend on a
 * calendar component.
 */
import type { Dayjs } from 'dayjs';

/** 取哪一天：本地日历上的那一天，还是 UTC 的那一天。Which day to read. */
export type ApiDateZone = 'local' | 'utc';

/**
 * 服务端契约的日期串：`YYYY-MM-DD`。名字里的 `Api` 指的就是这个用途——凡是发给
 * 接口的日期都走它，别处拿到 `YYYY-MM-DD` 也应当能追溯到同一个来源。
 *
 * 固定格式来自服务端的校验：`agent_evolution_service.py` 的 `parse_date` 比对
 * `isoformat()`，形状不符即拒绝。给人看的月份名、周名走 `Intl`（见 `calendar.ts` 的
 * `weekdayNames` / `monthTitle`），两者用途不同。
 *
 * `zone` 决定取哪一天，因为不同接口的约定不同：
 * - `'local'`（默认）：日期筛选，用户在本地日历上选的那一天就是这一天。
 * - `'utc'`：`agent_evolution` 的列表接口，服务端用 `timezone.utc` 拼边界，
 *   东八区的本地零点在 UTC 是前一天，必须按 UTC 取日期。
 *
 * The API contract's date string: `YYYY-MM-DD`. The `Api` in the name is that purpose —
 * every date sent to an endpoint goes through it, and any `YYYY-MM-DD` elsewhere should
 * trace back to the same source.
 *
 * The fixed shape comes from the server's validation: `parse_date` in
 * `agent_evolution_service.py` compares against `isoformat()` and rejects anything else.
 * Human-facing month and weekday names go through `Intl` (see `weekdayNames` /
 * `monthTitle` in `calendar.ts`); the two serve different purposes.
 *
 * `zone` picks which day is read, because endpoints differ:
 * - `'local'` (the default): date filters, where the day the user picked on their own
 *   calendar is the day that counts.
 * - `'utc'`: the `agent_evolution` list endpoints, whose bounds the server builds with
 *   `timezone.utc` — a local midnight in UTC+8 is the previous day in UTC.
 */
export function toApiDate(date: Dayjs, zone: ApiDateZone = 'local'): string {
  return zone === 'utc'
    ? date.toDate().toISOString().slice(0, 10)
    : date.format('YYYY-MM-DD');
}
