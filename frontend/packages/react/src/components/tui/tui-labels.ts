import type { TuiLabels } from './types.js';
import { ZH_TUI_LABELS } from './tui-labels-zh.js';

/** 补齐当前语言的默认界面文案。 */
export type ResolvedTuiLabels = Required<TuiLabels>;

export const DEFAULT_TUI_LABELS: ResolvedTuiLabels = {
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
};

export function resolveTuiLabels(
  labels?: TuiLabels,
  locale: 'en' | 'zh' = 'en',
): ResolvedTuiLabels {
  const defaults = locale === 'zh' ? ZH_TUI_LABELS : DEFAULT_TUI_LABELS;
  if (!labels) return defaults;
  return {
    ...defaults,
    placeholder: labels.placeholder ?? defaults.placeholder,
    unavailableMessage:
      labels.unavailableMessage ?? defaults.unavailableMessage,
    send: labels.send ?? defaults.send,
    allCommands: labels.allCommands ?? defaults.allCommands,
    historyCommands: labels.historyCommands ?? defaults.historyCommands,
    suggestions: labels.suggestions ?? defaults.suggestions,
    noSuggestions: labels.noSuggestions ?? defaults.noSuggestions,
    pending: labels.pending ?? defaults.pending,
    success: labels.success ?? defaults.success,
    error: labels.error ?? defaults.error,
    copy: labels.copy ?? defaults.copy,
    copied: labels.copied ?? defaults.copied,
    empty: labels.empty ?? defaults.empty,
    noResult: labels.noResult ?? defaults.noResult,
    justNow: labels.justNow ?? defaults.justNow,
    formatMinutesAgo: labels.formatMinutesAgo ?? defaults.formatMinutesAgo,
    formatHoursAgo: labels.formatHoursAgo ?? defaults.formatHoursAgo,
    availableCommands: labels.availableCommands ?? defaults.availableCommands,
    helpHint: labels.helpHint ?? defaults.helpHint,
    parameters: labels.parameters ?? defaults.parameters,
    examples: labels.examples ?? defaults.examples,
    noParameters: labels.noParameters ?? defaults.noParameters,
    loadMore: labels.loadMore ?? defaults.loadMore,
    resource: labels.resource ?? defaults.resource,
    parameter: labels.parameter ?? defaults.parameter,
    required: labels.required ?? defaults.required,
    description: labels.description ?? defaults.description,
    defaultValue: labels.defaultValue ?? defaults.defaultValue,
    yes: labels.yes ?? defaults.yes,
    no: labels.no ?? defaults.no,
  };
}
