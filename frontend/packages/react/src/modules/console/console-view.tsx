import { useMonitoringBinding } from '../../shared/monitoring/context.js';
import { readData, tagData } from '../../shared/monitoring/data.js';
import { consoleLabels, useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { ConsoleTaskNotifications } from './task-notifications.js';
import { formatConsoleTaskResult } from './task-records.js';
import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import { useOvSignal } from '../../client/provider.js';
import { useId, useMemo, useRef } from 'react';
import { CommandConsole } from '../../components/command-console/index.js';
import { VikingBotSessionActions } from '../../components/viking-bot/index.js';
import { useConsole } from './use-console.js';
import { useConversation } from './use-conversation.js';
import {
  consoleCommandGroups,
  consoleQuickCommands,
} from './command-catalog.js';
import { ConsoleToolbar } from './console-toolbar.js';
import {
  ConsoleCollectionError,
  ConsoleConversationPanel,
} from './console-conversation-panel.js';
import { useBotActivation } from './use-bot-activation.js';
import { useConversationDraftIdentity } from './use-conversation-draft-identity.js';
import type { ConsoleMode, ConsoleProps } from './types.js';

const defaultModes: readonly ConsoleMode[] = ['conversation', 'command'];

export function ConsoleView(props: ConsoleProps) {
  const {
    mode: _mode,
    modes = defaultModes,
    defaultMode: _defaultMode,
    locale: localeProp,
    quickCommands,
    scopeKey,
    commandHistoryKey: _commandHistoryKey,
    requestOptions,
    onModeChange: _onModeChange,
    onOpenUri,
    conversation: _conversation,
    command,
    chatOptions,
    taskService: _taskService,
    commandOptions: _commandOptions,
    slots,
    labels: overrides,
    className,
    ...rest
  } = props;
  const locale = useLocale(localeProp);
  const openCommandUri = command?.onOpenUri ?? onOpenUri;
  const renderErrors = useRenderErrors(locale);
  const inheritedSignal = useOvSignal();
  const activation = useBotActivation({
    activation: chatOptions?.activation,
    enabled: modes.includes('conversation'),
    scopeKey: chatOptions?.scopeKey ?? scopeKey,
    signal:
      (chatOptions?.requestOptions ?? requestOptions)?.signal ??
      inheritedSignal,
  });
  const { status } = activation.state;
  const chatReady = status === 'ready';
  const visibleModes =
    status === 'disabled' ||
    status === 'checking' ||
    status === 'collection-error'
      ? modes.filter((item) => item !== 'conversation')
      : modes;
  const showConversation =
    visibleModes.includes('conversation') ||
    (status === 'checking' && !visibleModes.length);
  const draftIdentity = useConversationDraftIdentity(props);
  const state = useConsole(
    { ...props, modes: visibleModes, locale },
    draftIdentity,
  );
  const commandRecords = useMemo(
    () => state.records.map(formatConsoleTaskResult),
    [state.records],
  );
  const monitoring = useMonitoringBinding();
  tagData(monitoring, commandRecords, readData(monitoring, [state.records]));
  const labels = consoleLabels(locale, overrides);
  const managedConversation = useConversation(
    { ...props, locale, modes: chatReady ? modes : [] },
    state.setConversationDraft,
    state.conversationDraft,
  );
  const { sessions, headerActions, feedback, ...conversationProps } =
    managedConversation;
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
        {feedback}
        <ConsoleCollectionError activation={activation} locale={botLocale} />
        <ConsoleTaskNotifications
          locale={locale}
          onOpenUri={openCommandUri}
          onOpenMemoryUri={conversationProps.onOpenUri ?? onOpenUri}
        />
        {state.mode ? (
          <ErrorBoundary message={renderErrors.toolbar} resetKeys={[scopeKey]}>
            <ConsoleToolbar
              mode={state.mode}
              modes={visibleModes}
              panelId={id}
              locale={locale}
              labels={labels}
              history={state.history}
              isExecuting={state.isExecuting}
              onModeChange={(next) => state.setMode(next)}
              onClear={() => {
                state.clear();
                fillCommand('');
              }}
              onRecall={fillCommand}
              conversationActions={
                chatReady ? (
                  <VikingBotSessionActions
                    sessions={sessions}
                    extra={headerActions}
                    onOpenUri={conversationProps.onOpenUri ?? onOpenUri}
                    onCopy={conversationProps.onCopy}
                    labels={botLabels}
                    locale={botLocale}
                  />
                ) : undefined
              }
            >
              {slots?.toolbar}
            </ConsoleToolbar>
          </ErrorBoundary>
        ) : null}
        {showConversation ? (
          <ConsoleConversationPanel
            id={`${id}-conversation`}
            active={state.mode === 'conversation' || state.mode === undefined}
            activation={activation}
            media={chatOptions?.activation?.media}
            conversation={conversationProps}
            draft={state.conversationDraft}
            onDraftChange={state.setConversationDraft}
            footer={slots?.conversationContext}
            onOpenUri={onOpenUri}
            locale={botLocale}
            labels={botLabels}
          />
        ) : null}
        {visibleModes.includes('command') ? (
          <div
            id={`${id}-command`}
            ref={commandPanel}
            className="ov-console__panel"
            hidden={state.mode !== 'command'}
          >
            <ErrorBoundary
              monitoringRegion="commands"
              message={renderErrors.command}
              resetKeys={[scopeKey]}
            >
              {slots?.commandHelp}
              <CommandConsole
                records={commandRecords}
                history={state.history}
                value={state.command}
                commands={state.commands}
                commandGroups={consoleCommandGroups(locale)}
                locale={locale}
                busy={state.isExecuting}
                quickCommands={quickCommands ?? consoleQuickCommands(locale)}
                onValueChange={state.setCommand}
                onSubmit={(next) => void state.execute(next)}
                onOpenUri={openCommandUri}
                labels={labels.commandConsole}
              />
            </ErrorBoundary>
          </div>
        ) : null}
      </div>
    </LocaleProvider>
  );
}
