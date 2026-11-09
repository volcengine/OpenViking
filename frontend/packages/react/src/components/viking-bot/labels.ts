import type { VikingBotLabels } from './model.js';

export const DEFAULT_LABELS: Required<VikingBotLabels> = {
  placeholder: 'Ask VikingBot anything',
  send: 'Send',
  stop: 'Stop',
  attach: 'Attach files',
  removeAttachment: 'Remove attachment',
  copy: 'Copy',
  copied: 'Copied',
  reasoning: 'Reasoning',
  thinking: 'Thinking',
  tool: 'Tool call',
  toolPending: 'Queued',
  toolRunning: 'Running',
  toolCompleted: 'Done',
  toolError: 'Failed',
  context: 'Context',
  empty: 'Start a conversation',
  newSession: 'New chat',
  retry: 'Retry',
  scrollToLatest: 'Scroll to latest',
  unavailable: 'VikingBot is unavailable',
};
