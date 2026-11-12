import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import { useId, useRef } from 'react';
import { Tui } from '../../components/tui/index.js';
import {
  VikingBot,
  VikingBotSessionActions,
} from '../../components/viking-bot/index.js';
import { useConsole } from './use-console.js';
import { consoleLabels } from './labels.js';
import { ConsoleToolbar } from './console-toolbar.js';
import type { ConsoleProps } from './types.js';

/** 终端空态默认展示的快捷命令，顺序即展示顺序。 */
const DEFAULT_QUICK_COMMANDS = ['help', 'ls', 'find', 'read', 'status'].map(
  (name) => ({ name, value: `ov ${name} ` }),
);

export function Console({
  mode,
  defaultMode,
  locale: localeProp,
  quickCommands = DEFAULT_QUICK_COMMANDS,
  scopeKey,
  requestOptions,
  onModeChange,
  onOpenUri,
  conversation,
  slots,
  labels: overrides,
  className,
  ...rest
}: ConsoleProps) {
  const locale = useLocale(localeProp);
  const state = useConsole({
    mode,
    defaultMode,
    locale,
    scopeKey,
    requestOptions,
    onModeChange,
  });
  const labels = consoleLabels(locale, overrides);
  const { sessions, headerActions, ...conversationProps } = conversation ?? {};
  const botLocale = conversationProps.locale ?? locale;
  const botLabels = {
    ...consoleLabels(botLocale, overrides).vikingBot,
    ...conversationProps.labels,
  };
  const id = useId();
  const commandPanel = useRef<HTMLDivElement>(null);
  const fillCommand = (command: string) => {
    state.setCommand(command);
    commandPanel.current
      ?.querySelector('textarea')
      ?.focus({ preventScroll: true });
  };
  return (
    <LocaleProvider locale={locale}>
      <div
        {...rest}
        className={['ov-console', className].filter(Boolean).join(' ')}
      >
        <ConsoleToolbar
          mode={state.mode}
          panelId={id}
          locale={locale}
          labels={labels}
          records={state.records}
          isExecuting={state.isExecuting}
          onModeChange={(next) => state.setMode(next)}
          onClear={() => {
            state.clear();
            fillCommand('');
          }}
          onRecall={fillCommand}
          conversationActions={
            <VikingBotSessionActions
              sessions={sessions}
              extra={headerActions}
              labels={botLabels}
              locale={botLocale}
            />
          }
        >
          {slots?.toolbar}
        </ConsoleToolbar>
        <div
          id={`${id}-conversation`}
          className="ov-console__panel"
          hidden={state.mode !== 'conversation'}
        >
          {state.mode === 'conversation' ? (
            <VikingBot
              {...conversationProps}
              onOpenUri={conversationProps.onOpenUri ?? onOpenUri}
              messages={conversationProps.messages ?? []}
              draft={conversationProps.draft ?? state.conversationDraft}
              onDraftChange={
                conversationProps.draft === undefined
                  ? (value) => {
                      state.setConversationDraft(value);
                      conversationProps.onDraftChange?.(value);
                    }
                  : conversationProps.onDraftChange
              }
              footer={slots?.conversationContext ?? conversationProps.footer}
              locale={botLocale}
              labels={botLabels}
            />
          ) : null}
        </div>
        <div
          id={`${id}-command`}
          ref={commandPanel}
          className="ov-console__panel"
          hidden={state.mode !== 'command'}
        >
          {slots?.commandHelp}
          <Tui
            records={state.records}
            value={state.command}
            commands={state.commands}
            locale={locale}
            disabled={state.isExecuting}
            quickCommands={quickCommands}
            onValueChange={state.setCommand}
            onSubmit={(command) => void state.execute(command)}
            onOpenUri={onOpenUri}
            labels={labels.tui}
          />
        </div>
      </div>
    </LocaleProvider>
  );
}
