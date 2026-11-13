import { TaskTrackingProvider } from '../../shared/task-tracking/context.js';
import { ConsoleTaskNotifications } from './task-notifications.js';
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

function ConsoleView({
  mode,
  defaultMode,
  locale: localeProp,
  quickCommands,
  scopeKey,
  requestOptions,
  onModeChange,
  onOpenUri,
  conversation,
  command,
  chatOptions,
  taskService,
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
    taskService,
    onModeChange,
  });
  const labels = consoleLabels(locale, overrides);
  const managedConversation = useConversation(
    { conversation, chatOptions, scopeKey, requestOptions, locale },
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
        <ConsoleTaskNotifications locale={locale} />
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
              onOpenUri={conversationProps.onOpenUri ?? onOpenUri}
              onCopy={conversationProps.onCopy}
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
          <CommandConsole
            records={state.records}
            value={state.command}
            commands={state.commands}
            commandGroups={consoleCommandGroups(locale)}
            locale={locale}
            disabled={state.isExecuting}
            quickCommands={quickCommands ?? consoleQuickCommands(locale)}
            onValueChange={state.setCommand}
            onSubmit={(next) => void state.execute(next)}
            onOpenUri={command?.onOpenUri ?? onOpenUri}
            labels={labels.commandConsole}
          />
        </div>
      </div>
    </LocaleProvider>
  );
}

export function Console(props: ConsoleProps) {
  return (
    <TaskTrackingProvider service={props.taskService}>
      <ConsoleView {...props} />
    </TaskTrackingProvider>
  );
}
