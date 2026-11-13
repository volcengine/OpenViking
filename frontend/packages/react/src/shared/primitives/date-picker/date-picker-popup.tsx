import { useMemo } from 'react';
import type { Dayjs } from 'dayjs';

import { toApiDate } from './api-date.js';
import {
  buildCalendar,
  monthTitle,
  shiftMonth,
  startOfDay,
  weekdayNames,
} from './calendar.js';

/**
 * 日历弹层：月份标题、上一月 / 下一月、周标题与 6×7 日期格。
 *
 * 从选择器里拆出来，两处职责不同：选择器管输入与开合，弹层只管把一个月画出来并
 * 报告选中的日期。
 *
 * The calendar popup: month title, previous/next month, weekday headings and a 6×7 grid.
 *
 * It is split from the picker because the two do different jobs: the picker owns the
 * field and the open state, the popup only draws one month and reports the picked day.
 */
export interface DatePickerPopupProps {
  year: number;
  month: number;
  /** 已选日期；`null` 表示未选。Picked date, or `null`. */
  selected: Dayjs | null;
  /** BCP 47 语言标签，决定月份与周标题的语言。BCP 47 tag for month and weekday headings. */
  locale: string;
  prevLabel: string;
  nextLabel: string;
  onShiftMonth: (delta: number) => void;
  onPick: (value: string) => void;
}

export function DatePickerPopup({
  year,
  month,
  selected,
  locale,
  prevLabel,
  nextLabel,
  onShiftMonth,
  onPick,
}: DatePickerPopupProps) {
  const cells = useMemo(() => buildCalendar(year, month), [year, month]);
  const weekdays = useMemo(() => weekdayNames(locale), [locale]);
  const title = useMemo(
    () => monthTitle(locale, year, month),
    [locale, year, month],
  );
  const selectedTime = selected ? startOfDay(selected).valueOf() : null;

  return (
    <div className="ov-date-picker__popup" role="dialog">
      <div className="ov-date-picker__header">
        <button
          type="button"
          className="ov-date-picker__nav"
          aria-label={prevLabel}
          onClick={() => onShiftMonth(-1)}
        >
          <svg
            aria-hidden="true"
            width={12}
            height={12}
            viewBox="0 0 16 16"
            fill="none"
          >
            <path
              d="M10 3 5 8l5 5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <span className="ov-date-picker__title">{title}</span>
        <button
          type="button"
          className="ov-date-picker__nav"
          aria-label={nextLabel}
          onClick={() => onShiftMonth(1)}
        >
          <svg
            aria-hidden="true"
            width={12}
            height={12}
            viewBox="0 0 16 16"
            fill="none"
          >
            <path
              d="m6 3 5 5-5 5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
      <div className="ov-date-picker__weekdays" aria-hidden="true">
        {weekdays.map((day) => (
          <span key={day}>{day}</span>
        ))}
      </div>
      <div className="ov-date-picker__grid">
        {cells.map((cell) => {
          const active = selectedTime === cell.date.valueOf();
          return (
            <button
              key={cell.date.valueOf()}
              type="button"
              className={[
                'ov-date-picker__cell',
                !cell.inMonth && 'ov-date-picker__cell--outside',
                cell.today && 'ov-date-picker__cell--today',
                active && 'ov-date-picker__cell--selected',
              ]
                .filter(Boolean)
                .join(' ')}
              aria-pressed={active}
              onClick={() => onPick(toApiDate(cell.date))}
            >
              {cell.date.date()}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export { shiftMonth };
