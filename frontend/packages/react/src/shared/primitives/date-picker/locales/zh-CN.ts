import type { DatePickerProps } from '../date-picker.js';

export default {
  calendarLabel: '展开日历',
  prevLabel: '上一月',
  nextLabel: '下一月',
  prevYearLabel: '上一年',
  nextYearLabel: '下一年',
  clearLabel: '清空',
} satisfies Required<
  Pick<
    DatePickerProps,
    | 'calendarLabel'
    | 'prevLabel'
    | 'nextLabel'
    | 'prevYearLabel'
    | 'nextYearLabel'
    | 'clearLabel'
  >
>;
