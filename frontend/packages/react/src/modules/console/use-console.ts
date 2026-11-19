import { bindingOf } from '../../shared/monitoring/client.js';
import { readData, tagData } from '../../shared/monitoring/data.js';
import { useCommandAnalytics } from './use-command-analytics.js';
import { useTrack } from '../../shared/analytics/use-track.js';
import { useTaskTracking } from '../../shared/task-tracking/context.js';
import { projectConsoleTasks } from './task-records.js';
import { useLocale } from '../../shared/locale-context.js';
import { localizeConsoleRecords } from './localize-records.js';
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { createOvClient } from '../../client/client.js';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import { consoleCommands } from './command-catalog.js';
import { ConsoleCommandError } from './command-messages.js';
import { createConsoleController } from './controller.js';
import { useConversationDraft } from './use-conversation-draft.js';
import type { ConsoleMode, ConsoleOptions, ConsoleResult } from './types.js';

/** Internal command execution and draft orchestration. */
export function useConsole(
  {
    mode: controlledMode,
    modes = ['conversation', 'command'],
    defaultMode = 'conversation',
    locale: localeProp,
    scopeKey,
    commandHistoryKey,
    onModeChange,
    requestOptions = {},
    taskService,
    commandOptions,
  }: ConsoleOptions = {},
  conversationIdentity?: object,
  conversationScopeKey?: string,
): ConsoleResult {
  const event = useTrack();
  const locale = useLocale(localeProp);
  const tracking = useTaskTracking({
    requestOptions,
    scopeKey,
    taskService,
    origin: 'command',
  });
  const analytics = useCommandAnalytics(tracking.tasks, tracking.source.key);
  const inherited = useOvClient('local');
  const inheritedSignal = useOvSignal();
  const [fallback] = useState(() => createOvClient());
  const client = inherited ?? fallback;
  const {
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal = inheritedSignal,
  } = requestOptions;
  const controller = useMemo(() => {
    // scopeKey explicitly invalidates dynamic identity functions without storing credentials.
    void scopeKey;
    return createConsoleController(
      client,
      {
        baseURL,
        apiKey,
        accountId,
        userId,
        resourceId,
        signal,
      },
      tracking.track,
      analytics,
      commandHistoryKey,
    );
  }, [
    client,
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal,
    scopeKey,
    commandHistoryKey,
    tracking.track,
    analytics,
  ]);
  const commandEnabled = modes.includes('command');
  // Effect necessity: commands keep network requests alive after user events.
  // Their lifetime follows this client/identity instance, which events cannot
  // observe on unmount or disable; cleanup aborts requests and invalidates late results.
  // Starting also restores this controller's browser-local history after client mount.
  useEffect(
    () => (commandEnabled ? controller.start() : undefined),
    [controller, commandEnabled],
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
  );
  const conversationDraft = useConversationDraft(
    controller,
    conversationIdentity,
    conversationScopeKey,
  );
  const [internalMode, setInternalMode] = useState<ConsoleMode>(defaultMode);
  const requestedMode = controlledMode ?? internalMode;
  const mode = modes.includes(requestedMode) ? requestedMode : modes[0];
  const records = useMemo(
    () =>
      localizeConsoleRecords(
        projectConsoleTasks(state.records, tracking.tasks),
        locale,
        commandOptions,
      ),
    [state.records, tracking.tasks, locale, commandOptions],
  );
  // Format library errors at read time; server errors keep their original identity.
  const error = useMemo(
    () =>
      state.error instanceof ConsoleCommandError
        ? new ConsoleCommandError(
            state.error.code,
            state.error.value,
            state.error.usage,
            locale,
          )
        : state.error,
    [state.error, locale],
  );
  tagData(
    bindingOf(client),
    records,
    readData(bindingOf(client), [state.records]),
  );
  return {
    ...state,
    ...conversationDraft,
    records,
    error,
    mode,
    commands: consoleCommands(locale, commandOptions),
    setMode(next: ConsoleMode) {
      if (next === mode || !modes.includes(next)) return;
      event(
        next === 'command'
          ? 'console_command_mode_select'
          : 'console_chat_mode_select',
      );
      if (controlledMode === undefined) setInternalMode(next);
      onModeChange?.(next);
    },
    setCommand: controller.setCommand,
    execute: (command?: string) =>
      controller.execute(command, locale, commandOptions),
    retry: () => controller.retry(locale, commandOptions),
    cancel: controller.cancel,
    clear: controller.clear,
  };
}
