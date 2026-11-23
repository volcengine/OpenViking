import type { DatePickerProps } from '../date-picker.js';
import type { DatePickerPopupProps } from '../date-picker-popup.js';

export default {
  calendarLabel: 'Open calendar',
  prevLabel: 'Previous month',
  nextLabel: 'Next month',
  prevYearLabel: 'Previous year',
  nextYearLabel: 'Next year',
  clearLabel: 'Clear',
} satisfies Required<
  Pick<
    DatePickerProps,
    'calendarLabel' | 'prevLabel' | 'nextLabel' | 'clearLabel'
  >
> &
  Pick<DatePickerPopupProps, 'prevYearLabel' | 'nextYearLabel'>;
