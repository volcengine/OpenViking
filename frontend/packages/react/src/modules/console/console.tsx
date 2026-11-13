import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import { useId, useRef } from 'react';
import { CommandConsole } from '../../components/command-console/index.js';
import {
  VikingBot,
  VikingBotSessionActions,
} from '../../components/viking-bot/index.js';
import { useConsole } from './use-console.js';
import { useConversation } from './use-conversation.js';
import { consoleCommandGroups, consoleQuickCommands } from './commands.js';
import { consoleLabels } from './labels.js';
import { ConsoleToolbar } from './console-toolbar.js';
import type { ConsoleProps } from './types.js';

export function Console({
  mode,
  modes,
  defaultMode,
  locale: localeProp,
  quickCommands,
  scopeKey,
  requestOptions,
  onModeChange,
  onOpenUri,
  conversation,
  chatOptions,
  slots,
  labels: overrides,
  className,
  ...rest
}: ConsoleProps) {
  const locale = useLocale(localeProp);
  const state = useConsole({
    mode,
    modes,
    defaultMode,
    locale,
    scopeKey,
    requestOptions,
    onModeChange,
  });
  const labels = consoleLabels(locale, overrides);
  const managedConversation = useConversation(
    { conversation, chatOptions, scopeKey, requestOptions, locale, modes },
    state.setConversationDraft,
  );
  const { sessions, headerActions, ...conversationProps } = managedConversation;
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
        {state.mode ? (
          <ConsoleToolbar
            mode={state.mode}
            modes={modes ?? ['conversation', 'command']}
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
                onOpenUri={conversationProps.onOpenUri ?? onOpenUri}
                onCopy={conversationProps.onCopy}
                labels={botLabels}
                locale={botLocale}
              />
            }
          >
            {slots?.toolbar}
          </ConsoleToolbar>
        ) : null}
        {modes === undefined || modes.includes('conversation') ? (
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
        ) : null}
        {modes === undefined || modes.includes('command') ? (
          <div
            id={`${id}-command`}
            ref={commandPanel}
            className="ov-console__panel"
            hidden={state.mode !== 'command'}
          >
            {slots?.commandHelp}
            <CommandConsole
              records={state.records}
              value={state.command}
              commands={state.commands}
              commandGroups={consoleCommandGroups(locale)}
              locale={locale}
              disabled={state.isExecuting}
              quickCommands={quickCommands ?? consoleQuickCommands(locale)}
              onValueChange={state.setCommand}
              onSubmit={(command) => void state.execute(command)}
              onOpenUri={onOpenUri}
              labels={labels.commandConsole}
            />
          </div>
        ) : null}
      </div>
    </LocaleProvider>
  );
}
