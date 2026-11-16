import { useAnalyticsRef } from '../../shared/analytics/context.js';
import { previewActionTarget } from '../../shared/analytics/preview-actions.js';
import {
  ErrorNotification,
  errorMessage,
} from '../../shared/feedback/error-notification.js';
import type { FilePreviewLabels } from './types.js';
import { LocaleProvider } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import { createContext, useContext, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { FilePreviewAction } from './action-types.js';
import type { FilePreviewTab } from './types.js';

interface CommandState {
  pending?: string;
  failure?: { reason: unknown; label: string };
}
const CommandContext = createContext<{
  pending: boolean;
  failure?: CommandState['failure'];
  execute: (action: FilePreviewAction, disabled: boolean) => Promise<void>;
} | null>(null);

/** Own execution for the preview lifetime, outside tab bodies and toolbar slots. */
export function FilePreviewCommandProvider({
  tab,
  children,
  locale,
  text,
}: {
  tab: FilePreviewTab;
  locale?: Locale;
  text: Required<FilePreviewLabels>;
  children: ReactNode;
}) {
  const analytics = useAnalyticsRef();
  const source =
    tab.kind === 'file'
      ? tab.source
      : tab.kind === 'directory'
        ? tab.levels[0].source
        : undefined;
  const scope = JSON.stringify(
    source?.path ? ['path', source.path] : ['tab', tab.key, source?.fileName],
  );
  const running = useRef(new Set<string>());
  const [states, setStates] = useState(() => new Map<string, CommandState>());
  const execute = async (action: FilePreviewAction, disabled: boolean) => {
    if (running.current.has(scope) || disabled || action.disabled) return;
    running.current.add(scope);
    setStates((current) =>
      new Map(current).set(scope, { pending: action.key }),
    );
    const target = previewActionTarget(action);
    if (target === 'copyContent')
      analytics.current?.emit('file_preview_copy_content_click', () => ({}));
    let failure: CommandState['failure'];
    try {
      await action.onClick();
      if (target === 'copyContent')
        analytics.current?.emit(
          'file_preview_copy_content_success',
          () => ({}),
        );
    } catch (reason) {
      if (!(reason instanceof Error && reason.name === 'AbortError'))
        failure = { reason, label: action.label };
      if (target === 'copyContent')
        analytics.current?.emit('file_preview_copy_content_fail', () => ({}));
    } finally {
      running.current.delete(scope);
      setStates((current) => {
        const next = new Map(current);
        if (failure) next.set(scope, { failure });
        else next.delete(scope);
        return next;
      });
    }
  };
  const state = states.get(scope);
  return (
    <LocaleProvider locale={locale}>
      <CommandContext.Provider
        value={{
          pending: state?.pending !== undefined,
          failure: state?.failure,
          execute,
        }}
      >
        {state?.failure ? (
          <ErrorNotification
            key={scope}
            title={`${state.failure.label} · ${text.actionFailed}`}
            identity={state.failure}
            error={state.failure.reason}
          >
            {errorMessage(state.failure.reason)}
          </ErrorNotification>
        ) : null}
        {children}
      </CommandContext.Provider>
    </LocaleProvider>
  );
}

export function useFilePreviewCommands() {
  return useContext(CommandContext)!;
}
