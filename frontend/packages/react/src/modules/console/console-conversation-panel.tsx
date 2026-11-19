import { useState, type ReactNode, type RefObject } from 'react';
import { ViewActivity } from '../../shared/view-activity.js';
import { NotificationScope } from '../../shared/feedback/notification-scope.js';
import {
  VikingBot,
  type VikingBotLabels,
  type VikingBotProps,
} from '../../components/viking-bot/index.js';
import {
  ErrorNotification,
  errorMessage,
} from '../../shared/feedback/error-notification.js';
import type { Locale } from '../../shared/locale.js';
import {
  ConsoleActivationCard,
  ConsoleActivationStatus,
} from './activation-card/activation-card.js';
import { consoleLabels } from './labels.js';
import type { useBotActivation } from './use-bot-activation.js';
import { useConsoleFocusRecovery } from './use-console-focus-recovery.js';

export function ConsoleConversationPanel({
  id,
  active,
  commandPanelRef,
  commandInputRef,
  activation,
  media,
  conversation,
  draft,
  onDraftChange,
  onOpenUri,
  footer,
  locale,
  labels,
}: {
  id: string;
  active: boolean;
  commandPanelRef: RefObject<HTMLDivElement | null>;
  commandInputRef: RefObject<HTMLTextAreaElement | null>;
  activation: ReturnType<typeof useBotActivation>;
  media?: ReactNode;
  conversation: Partial<VikingBotProps>;
  draft: string;
  onDraftChange: (value: string) => void;
  onOpenUri?: (uri: string) => void;
  footer?: ReactNode;
  locale: Locale;
  labels: VikingBotLabels;
}) {
  const { state } = activation;
  const recoverFocus = useConsoleFocusRecovery(
    commandPanelRef,
    commandInputRef,
  );
  const [visited, setVisited] = useState(active);
  if (active && !visited) setVisited(true);
  const failed =
    state.status === 'collection-error' ||
    state.status === 'load-error' ||
    state.status === 'start-error';
  return (
    <div
      id={id}
      ref={recoverFocus}
      className="ov-console__panel ov-console__panel--conversation"
      tabIndex={-1}
      hidden={!active}
    >
      <ConsoleActivationStatus
        status={state.status}
        identity={state}
        active={active}
        locale={locale}
      />
      {visited && state.status === 'ready' ? (
        <ViewActivity active={active}>
          <NotificationScope active={active} locale={locale}>
            <VikingBot
              {...conversation}
              onOpenUri={conversation.onOpenUri ?? onOpenUri}
              messages={conversation.messages ?? []}
              draft={conversation.draft ?? draft}
              onDraftChange={
                conversation.draft === undefined
                  ? (value) => {
                      onDraftChange(value);
                      conversation.onDraftChange?.(value);
                    }
                  : conversation.onDraftChange
              }
              footer={footer ?? conversation.footer}
              locale={locale}
              labels={labels}
            />
          </NotificationScope>
        </ViewActivity>
      ) : null}
      {active && state.status !== 'ready' && state.status !== 'disabled' ? (
        <ConsoleActivationCard
          status={
            state.status === 'checking' || state.status === 'cancelled'
              ? state.status
              : state.status === 'starting'
                ? 'activating'
                : failed
                  ? 'error'
                  : 'inactive'
          }
          onAction={(event) => {
            const action = event.currentTarget;
            // Keep keyboard focus in this stable panel before its action is removed.
            if (action.ownerDocument.activeElement === action)
              action
                .closest<HTMLElement>('.ov-console__panel--conversation')
                ?.focus({ preventScroll: true });
            if (failed) activation.retry();
            else activation.start();
          }}
          media={media}
          locale={locale}
        />
      ) : null}
      {failed ? (
        <NotificationScope active={active} locale={locale}>
          <ErrorNotification
            title={consoleLabels(locale).conversation}
            error={state.error}
            identity={state}
            locale={locale}
          >
            {errorMessage(state.error)}
          </ErrorNotification>
        </NotificationScope>
      ) : null}
    </div>
  );
}
