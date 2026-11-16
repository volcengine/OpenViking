import { observeCommand, tagCommandRecords } from './command-monitoring.js';
import type { MonitoringSpan } from '../../shared/monitoring/trace/span.js';
import type { CommandAnalytics } from './use-command-analytics.js';
import type { TaskReference } from '../../shared/task-tracking/types.js';
import type { SetStateAction } from 'react';
import type { Locale } from '../../shared/locale.js';
import {
  ConsoleCommandError,
  formatConsoleMessage,
  commandErrorText,
  type ConsoleMessage,
  type ConsoleRecord,
} from './command-messages.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { ConsoleState } from './types.js';
import { executeCommand } from './commands.js';
import { tokenize, parseArguments } from './command-parser.js';

export function createConsoleController(
  client: OvClient,
  options: OvRequestOptions = {},
  trackTask?: (task: TaskReference, origin?: MonitoringSpan) => string,
  analytics?: CommandAnalytics,
) {
  let state: Omit<ConsoleState, 'records'> & {
    records: readonly ConsoleRecord[];
  } = {
    records: [],
    command: '',
    conversationDraft: '',
    isExecuting: false,
  };
  const listeners = new Set<() => void>();
  let running: AbortController | undefined;
  let active = false;
  let sequence = 0;
  let cancelRunning: ((report?: boolean) => void) | undefined;
  const update = (patch: Partial<ConsoleState>) => {
    tagCommandRecords(client, patch.records);
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  const cancel = () => cancelRunning?.(true);
  const clear = () => {
    cancel();
    update({
      records: [],
      command: '',
      error: undefined,
      failedCommand: undefined,
    });
  };
  async function execute(input = state.command, locale: Locale = 'en-US') {
    const command = input.trim();
    if (!active || running || !command) return;
    try {
      const tokens = tokenize(command);
      if (tokens[0]?.value === 'clear') {
        parseArguments(tokens.slice(1), []);
        clear();
        update({ command: '' });
        return;
      }
    } catch {
      // executeCommand reports parsing errors through the same record lifecycle.
    }
    const controller = new AbortController();
    const signal = options.signal
      ? AbortSignal.any([options.signal, controller.signal])
      : controller.signal;
    running = controller;
    const id = `command-${++sequence}`;
    const event = analytics?.begin(id);
    const monitoring = observeCommand(client, signal);
    const current = () => active && running === controller;
    const finish = (message: ConsoleMessage) => {
      if (!current()) return;
      running = undefined;
      cancelRunning = undefined;
      update({
        isExecuting: false,
        records: state.records.map((record) =>
          record.id === id
            ? {
                ...record,
                status: 'error',
                localMessage: message,
                content: {
                  variant: 'text',
                  text: formatConsoleMessage(message, 'en-US'),
                },
              }
            : record,
        ),
      });
    };
    const onAbort = () => finish({ code: 'cancelled' });
    cancelRunning = (report = false) => {
      if (report) event?.cancel();
      controller.abort();
      signal.removeEventListener('abort', onAbort);
    };
    update({
      command: '',
      isExecuting: true,
      error: undefined,
      failedCommand: undefined,
      records: [
        ...state.records,
        { id, command, status: 'pending', createdAtMs: Date.now() },
      ],
    });
    signal.addEventListener('abort', onAbort, { once: true });
    try {
      if (signal.aborted) {
        onAbort();
        return;
      }
      let taskKeys: string[] = [];
      const content = await executeCommand(
        command,
        monitoring?.client ?? client,
        { ...options, signal },
        locale,
        (tasks) => {
          if (current() && !signal.aborted && trackTask)
            taskKeys = tasks.map((task) => trackTask(task, monitoring?.span));
        },
      );
      if (!current() || signal.aborted) return;
      monitoring?.finish(content);
      event?.success(taskKeys);
      update({
        records: state.records.map((record) =>
          record.id === id
            ? { ...record, status: 'success', content, taskKeys }
            : record,
        ),
      });
    } catch (error) {
      if (!current() || signal.aborted) return;
      monitoring?.finish();
      event?.fail(error);
      const localMessage =
        error instanceof ConsoleCommandError
          ? { code: error.code, value: error.value, usage: error.usage }
          : undefined;
      update({
        error,
        failedCommand: command,
        records: state.records.map((record) =>
          record.id === id
            ? {
                ...record,
                status: 'error',
                localMessage,
                content: { variant: 'text', text: commandErrorText(error) },
              }
            : record,
        ),
      });
    } finally {
      signal.removeEventListener('abort', onAbort);
      if (current()) {
        running = undefined;
        cancelRunning = undefined;
        update({ isExecuting: false });
      }
    }
  }
  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => state,
    start() {
      active = true;
      return () => {
        cancelRunning?.();
        active = false;
      };
    },
    execute,
    cancel,
    clear,
    setCommand: (command: string) => update({ command }),
    setConversationDraft: (conversationDraft: SetStateAction<string>) =>
      update({
        conversationDraft:
          typeof conversationDraft === 'function'
            ? conversationDraft(state.conversationDraft)
            : conversationDraft,
      }),
    retry: (locale: Locale) =>
      state.failedCommand
        ? execute(state.failedCommand, locale)
        : Promise.resolve(),
  };
}
