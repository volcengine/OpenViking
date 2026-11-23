import { observeCommand, tagCommandRecords } from './command-monitoring.js';
import type { MonitoringSpan } from '../../shared/monitoring/trace/span.js';
import type { CommandAnalytics } from './use-command-analytics.js';
import type { TaskReference } from '../../shared/task-tracking/types.js';
import type { SetStateAction } from 'react';
import type { Locale } from '../../shared/locale.js';
import {
  cancelledCommandRecord,
  failedCommandRecord,
  type ConsoleRecord,
} from './command-messages.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { ConsoleCommandOptions, ConsoleState } from './types.js';
import { executeCommand } from './commands.js';
import { clearRequested } from './command-parser.js';
import {
  appendCommandHistory,
  clearCommandHistory,
  readCommandHistory,
} from './command-history-storage.js';

export function createConsoleController(
  client: OvClient,
  options: OvRequestOptions = {},
  trackTask?: (task: TaskReference, origin?: MonitoringSpan) => string,
  analytics?: CommandAnalytics,
  commandHistoryKey?: string,
) {
  let state: Omit<ConsoleState, 'records'> & {
    records: readonly ConsoleRecord[];
  } = {
    records: [],
    history: [],
    command: '',
    conversationDraft: '',
    isExecuting: false,
  };
  const listeners = new Set<() => void>();
  let running: AbortController | undefined;
  let active = false;
  let historyRestored = false;
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
    const history = commandHistoryKey
      ? clearCommandHistory(commandHistoryKey)
      : [];
    update({
      records: [],
      history,
      command: '',
      error: undefined,
      failedCommand: undefined,
    });
  };
  async function execute(
    input = state.command,
    locale: Locale = 'en-US',
    commandOptions?: ConsoleCommandOptions,
  ) {
    const command = input.trim();
    if (!active || running || !command) return;
    if (clearRequested(command)) {
      clear();
      return;
    }
    const controller = new AbortController();
    const signal = options.signal
      ? AbortSignal.any([options.signal, controller.signal])
      : controller.signal;
    running = controller;
    const id = `command-${++sequence}`;
    const createdAtMs = Date.now();
    const entry = { id, command, createdAtMs };
    const history = commandHistoryKey
      ? (appendCommandHistory(commandHistoryKey, entry) ?? state.history)
      : [...state.history, entry];
    const event = analytics?.begin(id);
    const monitoring = observeCommand(client, signal);
    const current = () => active && running === controller;
    const updateRecord = (
      fields: Partial<ConsoleRecord>,
      patch: Partial<ConsoleState> = {},
    ) =>
      update({
        ...patch,
        records: state.records.map((record) =>
          record.id === id ? { ...record, ...fields } : record,
        ),
      });
    const onAbort = () => {
      if (!current()) return;
      running = undefined;
      cancelRunning = undefined;
      updateRecord(cancelledCommandRecord(), { isExecuting: false });
    };
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
      history,
      records: [...state.records, { ...entry, status: 'pending' }],
    });
    signal.addEventListener('abort', onAbort, { once: true });
    try {
      if (signal.aborted) {
        onAbort();
        return;
      }
      let taskKeys: string[] = [];
      const { content, telemetry, resultNotice } = await executeCommand(
        command,
        monitoring?.client ?? client,
        { ...options, signal },
        locale,
        (tasks) => {
          if (current() && !signal.aborted && trackTask)
            taskKeys = tasks.map((task) => trackTask(task, monitoring?.span));
        },
        commandOptions,
      );
      if (!current() || signal.aborted) return;
      monitoring?.finish(content);
      event?.success(taskKeys);
      updateRecord({
        status: 'success',
        content,
        taskKeys,
        telemetry,
        resultNotice,
      });
    } catch (error) {
      if (!current() || signal.aborted) return;
      monitoring?.finish();
      event?.fail(error);
      updateRecord(failedCommandRecord(error), {
        error,
        failedCommand: command,
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
      if (commandHistoryKey && !historyRestored) {
        historyRestored = true;
        const history = readCommandHistory(commandHistoryKey);
        if (history.length) update({ history });
      }
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
    retry: (locale: Locale, commandOptions?: ConsoleCommandOptions) =>
      state.failedCommand
        ? execute(state.failedCommand, locale, commandOptions)
        : Promise.resolve(),
  };
}
