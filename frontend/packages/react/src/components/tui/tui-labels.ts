import type { TuiLabels } from './types.js';

/** 组件内部使用的完整文案；公开的 `TuiLabels` 只覆盖其中一部分。 */
export interface ResolvedTuiLabels {
  placeholder: string;
  unavailableMessage: string;
  send: string;
  suggestions: string;
  noSuggestions: string;
  pending: string;
  success: string;
  error: string;
  copy: string;
  copied: string;
  empty: string;
  parameters: string;
  options: string;
  examples: string;
  noParameters: string;
  loadMore: string;
}

export const DEFAULT_TUI_LABELS: ResolvedTuiLabels = {
  placeholder: 'Type a command, for example /find "release notes"',
  unavailableMessage: 'Commands are unavailable right now.',
  send: 'Send',
  suggestions: 'Command suggestions',
  noSuggestions: 'No matching commands',
  pending: 'Running',
  success: 'Succeeded',
  error: 'Failed',
  copy: 'Copy',
  copied: 'Copied',
  empty: 'Run a command to see its result here.',
  parameters: 'Parameters',
  options: 'Options',
  examples: 'Examples',
  noParameters: 'No parameters',
  loadMore: 'Load more',
};

export function resolveTuiLabels(labels?: TuiLabels): ResolvedTuiLabels {
  if (!labels) return DEFAULT_TUI_LABELS;
  return {
    placeholder: labels.placeholder ?? DEFAULT_TUI_LABELS.placeholder,
    unavailableMessage:
      labels.unavailableMessage ?? DEFAULT_TUI_LABELS.unavailableMessage,
    send: labels.send ?? DEFAULT_TUI_LABELS.send,
    suggestions: labels.suggestions ?? DEFAULT_TUI_LABELS.suggestions,
    noSuggestions: labels.noSuggestions ?? DEFAULT_TUI_LABELS.noSuggestions,
    pending: labels.pending ?? DEFAULT_TUI_LABELS.pending,
    success: labels.success ?? DEFAULT_TUI_LABELS.success,
    error: labels.error ?? DEFAULT_TUI_LABELS.error,
    copy: labels.copy ?? DEFAULT_TUI_LABELS.copy,
    copied: labels.copied ?? DEFAULT_TUI_LABELS.copied,
    empty: labels.empty ?? DEFAULT_TUI_LABELS.empty,
    parameters: DEFAULT_TUI_LABELS.parameters,
    options: DEFAULT_TUI_LABELS.options,
    examples: DEFAULT_TUI_LABELS.examples,
    noParameters: DEFAULT_TUI_LABELS.noParameters,
    loadMore: DEFAULT_TUI_LABELS.loadMore,
  };
}
