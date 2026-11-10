import type { ResolvedTuiLabels } from '../tui-labels.js';

const messages = {
  placeholder: '>_ Enter CLI commands, e.g. ov find OpenViking',
  unavailableMessage: 'Commands are unavailable right now.',
  send: 'Send',
  allCommands: 'All commands',
  historyCommands: 'History',
  suggestions: 'Command suggestions',
  noSuggestions: 'No matching commands',
  pending: 'Running',
  success: 'Succeeded',
  error: 'Failed',
  copy: 'Copy',
  copied: 'Copied',
  empty: 'Run a command to see its result here.',
  noResult: 'No result returned',
  justNow: 'Just now',
  formatMinutesAgo: (minutes) =>
    `${minutes} minute${minutes === 1 ? '' : 's'} ago`,
  formatHoursAgo: (hours) => `${hours} hour${hours === 1 ? '' : 's'} ago`,
  availableCommands: 'Available commands',
  helpHint: 'Type / to open the command palette.',
  parameters: 'Parameters',
  examples: 'Examples',
  noParameters: 'No parameters',
  loadMore: 'Load more',
  resource: 'Resource',
  parameter: 'Parameter',
  required: 'Required',
  description: 'Description',
  defaultValue: 'Default',
  yes: 'Yes',
  no: 'No',
} satisfies ResolvedTuiLabels;

export default messages;
