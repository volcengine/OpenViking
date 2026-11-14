import type { InteractionLabels } from '../labels.js';

const messages = {
  directoryTarget: 'Output directory',
  directorySelected: 'Selected target:',
  directorySearch: 'Directory name',
  directoryEmpty: 'No search results',
  directoryPermissionChecking: 'Reading directory permissions',
  directoryPermissionWritable:
    'This directory is writable. Permissions will be checked again on submit.',
  directoryPermissionHint:
    'Choose a writable directory. Creating a child requires edit or manage permission.',
  directoryNotWritable:
    'This directory is read-only. Choose another directory.',
  directoryUnavailable: 'Choose an existing directory',
  directoryCheckFailed:
    'Could not verify the directory and write permission. Retry or choose another directory.',
  directoryCreate: 'New subdirectory',
  directoryCreateTitle: 'Subdirectory',
  directoryName: 'Directory name',
  directoryNameHint:
    'Enter one child directory name under the parent above, not a full path.',
  directoryNameInvalid:
    'Enter one directory name without path separators, URL reserved characters, or surrounding spaces.',
  directoryCreateConfirm: 'Create and select',
  directoryCreateFailed:
    'Could not create the directory. Check permissions and retry.',
  directoryRetry: 'Retry',
  directoryLoadFailed: 'Could not load directories',
  directoryParent: (uri) => `Parent directory: ${uri}`,
  directoryPickerTitle: 'Choose a save directory',
  directoryPickerConfirm: 'Use this directory',
  directoryPickerHint: 'Choose a directory',
  answer: 'Answer',
  title: 'Needs your input',
  submit: 'Submit',
  cancel: 'Cancel',
  other: 'Other',
  submitted: 'Submitted',
  cancelled: 'Cancelled',
  progress: (index, total) => `${index + 1} / ${total}`,
  scheduleNone: 'Do not update',
  scheduleDaily: 'Daily',
  scheduleWeekly: 'Weekly',
  scheduleTime: 'Update time',
  scheduleWeekday: 'Update day',
  scheduleFrequency: 'Update frequency',
  scheduleHour: 'Hours',
  scheduleMinute: 'Minutes',
  required: 'Please complete this question',
  timeInvalid: 'Enter a valid time in HH:mm format (00:00–23:59)',
  weekdayRequired: 'Choose a day for the weekly update',
  submitFailed: 'Submission failed. Please try again.',
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
