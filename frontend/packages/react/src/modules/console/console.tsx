import { useId, useRef } from 'react';
import { Tui } from '../../components/tui/index.js';
import { VikingBot } from '../../components/viking-bot/index.js';
import { useConsole } from './use-console.js';
import { consoleLabels } from './labels.js';
import { ConsoleToolbar } from './console-toolbar.js';
import { ConsoleEmpty } from './console-empty.js';
import type { ConsoleProps } from './types.js';

export function Console({
  mode,
  defaultMode,
  locale = 'en',
  scopeKey,
  requestOptions,
  onModeChange,
  onOpenUri,
  slots,
  labels: overrides,
  className,
  ...rest
}: ConsoleProps) {
  const state = useConsole({
    mode,
    defaultMode,
    locale,
    scopeKey,
    requestOptions,
    onModeChange,
  });
  const labels = consoleLabels(locale, overrides);
  const id = useId();
  const commandPanel = useRef<HTMLDivElement>(null);
  const fillCommand = (command: string) => {
    state.setCommand(command);
    commandPanel.current
      ?.querySelector('textarea')
      ?.focus({ preventScroll: true });
  };
  return (
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
            messages={[]}
            draft={state.conversationDraft}
            onDraftChange={state.setConversationDraft}
            footer={slots?.conversationContext}
            emptyState={<p role="status">{labels.description}</p>}
            labels={labels.vikingBot}
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
          emptyState={
            <ConsoleEmpty
              commands={state.commands}
              labels={labels}
              onSelect={fillCommand}
            />
          }
          onValueChange={state.setCommand}
          onSubmit={(command) => void state.execute(command)}
          onOpenUri={onOpenUri}
          labels={labels.tui}
        />
      </div>
    </div>
  );
}
