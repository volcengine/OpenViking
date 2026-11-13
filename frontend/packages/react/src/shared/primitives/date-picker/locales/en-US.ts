import type { DatePickerProps } from '../date-picker.js';

export default {
  calendarLabel: 'Open calendar',
  prevLabel: 'Previous month',
  nextLabel: 'Next month',
  clearLabel: 'Clear',
} satisfies Required<
  Pick<
    DatePickerProps,
    'calendarLabel' | 'prevLabel' | 'nextLabel' | 'clearLabel'
  >
>;
