import type { InteractionLabels } from '../labels.js';

const messages = {
  answer: 'Answer',
  title: 'Needs your input',
  submit: 'Submit',
  cancel: 'Cancel',
  other: 'Other',
  submitted: 'Submitted',
  cancelled: 'Cancelled',
  progress: (index, total) => `${index + 1} / ${total}`,
  scheduleNone: 'Do not schedule',
  scheduleDaily: 'Every day',
  scheduleWeekly: 'Every week',
  scheduleTime: 'Time',
  scheduleWeekday: 'Weekday',
  weekdays: [
    'Monday',
    'Tuesday',
    'Wednesday',
    'Thursday',
    'Friday',
    'Saturday',
    'Sunday',
  ],
} satisfies InteractionLabels;

export default messages;
