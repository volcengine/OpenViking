import { ChevronRightIcon } from '../../icons/index.js';
import {
  Dropdown,
  DropdownItem,
} from '../../shared/primitives/dropdown/index.js';
import type { ExperienceDashboardLabels } from './types.js';

/** 可选区间：天数（`0` 表示不限时间）与对应的文案键。 Day count and its label key. */
const OPTIONS = [
  { days: 3, key: 'day3' },
  { days: 7, key: 'week' },
  { days: 30, key: 'month' },
  { days: 0, key: 'all' },
] as const;

/**
 * 统计时间范围：一个「最近 N 天」的文本触发器，点开后是快捷区间列表。
 *
 * 火山版在同一浮层里还放了日历，用来选任意区间；这里的契约只有天数
 * （`days` + `onDaysChange`），因此只保留快捷区间，触发器与浮层的视觉规则
 * 与火山版一致。
 *
 * The statistics range: a "last N days" text trigger that opens a shortcut list.
 *
 * The console puts a calendar beside the shortcuts for picking an arbitrary range;
 * this component's contract is a day count (`days` + `onDaysChange`), so only the
 * shortcuts remain, with the console's trigger and surface styling.
 */
export function ExperienceTimeRange({
  days,
  labels: t,
  onChange,
}: {
  days: number;
  labels: ExperienceDashboardLabels;
  onChange: (days: number) => void;
}) {
  const current = OPTIONS.find((option) => option.days === days) ?? OPTIONS[3];
  return (
    <Dropdown
      className="ov-experience-dashboard__range-popover"
      menuClassName="ov-experience-dashboard__range-menu"
      trigger={
        <button
          type="button"
          className="ov-experience-dashboard__range-trigger"
          aria-label={t.range}
        >
          <span>{t[current.key]}</span>
          <ChevronRightIcon
            aria-hidden="true"
            width={12}
            height={12}
            className="ov-experience-dashboard__range-chevron"
          />
        </button>
      }
    >
      {OPTIONS.map((option) => (
        <DropdownItem
          key={option.days}
          role="menuitemradio"
          aria-checked={option === current}
          className="ov-experience-dashboard__range-option"
          onClick={() => onChange(option.days)}
        >
          {t[option.key]}
        </DropdownItem>
      ))}
    </Dropdown>
  );
}
