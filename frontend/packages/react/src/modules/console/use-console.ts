import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { createOvClient } from '../../client/client.js';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import { consoleCommands } from './commands.js';
import { createConsoleController } from './controller.js';
import type { ConsoleMode, ConsoleOptions, ConsoleResult } from './types.js';

/** Native command execution and draft orchestration, with or without Console. */
export function useConsole({
  mode: controlledMode,
  defaultMode = 'conversation',
  locale = 'en',
  scopeKey,
  onModeChange,
  requestOptions = {},
}: ConsoleOptions = {}): ConsoleResult {
  const inherited = useOvClient();
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
    return createConsoleController(client, {
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal,
    });
  }, [
    client,
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal,
    scopeKey,
  ]);
  // Effect necessity: commands keep network requests alive after user events.
  // Their lifetime follows this client/identity instance, which events cannot
  // observe on unmount; cleanup aborts requests and invalidates late results.
  useEffect(() => controller.start(), [controller]);
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  const [internalMode, setInternalMode] = useState<ConsoleMode>(defaultMode);
  const mode = controlledMode ?? internalMode;
  return {
    ...state,
    mode,
    commands: consoleCommands(locale),
    setMode(next: ConsoleMode) {
      if (next === mode) return;
      if (controlledMode === undefined) setInternalMode(next);
      onModeChange?.(next);
    },
    setCommand: controller.setCommand,
    setConversationDraft: controller.setConversationDraft,
    execute: (command?: string) => controller.execute(command, locale),
    retry: () => controller.retry(locale),
    cancel: controller.cancel,
    clear: controller.clear,
  };
}
