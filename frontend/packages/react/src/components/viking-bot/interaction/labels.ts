import type { VikingBotInteractionLabels } from './types.js';

/** 交互区实际使用的文案类型，含表单控件所需的频率与星期名称。 */
export type InteractionLabels = Required<
  Omit<VikingBotInteractionLabels, 'progress'>
> & {
  progress: (index: number, total: number) => string;
  scheduleNone: string;
  scheduleDaily: string;
  scheduleWeekly: string;
  weekdays: readonly string[];
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
