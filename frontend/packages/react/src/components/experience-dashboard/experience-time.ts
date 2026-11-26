import dayjs from 'dayjs';

/**
 * 轨迹时间：用 dayjs 解析，交给 `Intl` 按界面语言格式化。
 *
 * 解析走 dayjs 是仓库约定（日期算术与解析统一用它）；`Intl` 只负责给人看的格式。
 * 解析不出来时原样返回，避免把无效时间显示成 1970。
 *
 * The trajectory time: parsed with dayjs, formatted by `Intl` for the interface
 * language.
 *
 * Parsing through dayjs is the repository rule (all date arithmetic and parsing uses
 * it); `Intl` only renders the human-facing format. An unparseable value is returned
 * as-is rather than shown as 1970.
 */
export function experienceTime(value: string, locale: string) {
  const time = dayjs(value);
  if (!time.isValid()) return value;
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(time.toDate());
}
