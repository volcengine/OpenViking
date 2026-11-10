import type { VikingBotInteractionLabels } from './types.js';

/** 内部完整文案类型，从公开类型派生，保证宿主可覆盖每一处文案。 */
export type InteractionLabels = Required<
  Omit<VikingBotInteractionLabels, 'progress'>
> & {
  progress: (index: number, total: number) => string;
};

/** 内置交互区文案。Built-in interaction copy. */
export const DEFAULT_INTERACTION_LABELS: InteractionLabels = {
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
};
