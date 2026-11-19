import { useState, type ReactNode } from 'react';
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
import { Button } from '../../shared/primitives/button/button.js';
import { ConsoleActivationCard } from './activation-card/activation-card.js';
import { consoleLabels } from './labels.js';
import type { useBotActivation } from './use-bot-activation.js';

export function ConsoleCollectionError({
  activation,
  locale,
}: {
  activation: ReturnType<typeof useBotActivation>;
  locale: Locale;
}) {
  const { state, retry } = activation;
  if (state.status !== 'collection-error') return null;
  const labels = consoleLabels(locale);
  return (
    <ErrorNotification
      title={labels.conversation}
      error={state.error}
      identity={state}
      locale={locale}
      actions={<Button onClick={retry}>{labels.vikingBot.retry}</Button>}
    >
      {errorMessage(state.error)}
    </ErrorNotification>
  );
}

export function ConsoleConversationPanel({
  id,
  active,
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
  const [visited, setVisited] = useState(active);
  if (active && !visited) setVisited(true);
  const failed =
    state.status === 'load-error' || state.status === 'start-error';
  return (
    <div
      id={id}
      className="ov-console__panel ov-console__panel--conversation"
      hidden={!active}
    >
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
            state.status === 'checking'
              ? 'checking'
              : state.status === 'starting'
                ? 'activating'
                : failed
                  ? 'error'
                  : 'inactive'
          }
          onAction={failed ? activation.retry : activation.start}
          media={media}
          locale={locale}
        />
      ) : null}
      {active && failed ? (
        <ErrorNotification
          title={consoleLabels(locale).conversation}
          error={state.error}
          identity={state}
          locale={locale}
        >
          {errorMessage(state.error)}
        </ErrorNotification>
      ) : null}
    </div>
  );
}
