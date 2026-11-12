import type { DatePickerProps } from '../date-picker.js';

export default {
  calendarLabel: '展开日历',
  prevLabel: '上一月',
  nextLabel: '下一月',
  clearLabel: '清空',
} satisfies Required<
  Pick<
    DatePickerProps,
    'calendarLabel' | 'prevLabel' | 'nextLabel' | 'clearLabel'
  >
>;
