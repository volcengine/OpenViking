import { Bubble } from '@ark-design/ui/bubble';
import { ErrorCard } from '@ark-design/ui/error-card';

import { MessageBody } from './ark-message-body.js';
import { messageText } from './message-groups.js';
import { MessageFooter } from './message-footer.js';
import type { VikingBotMessage, VikingBotStreamingState } from './model.js';
import type { VikingBotMessageListProps } from './types.js';

type Labels = NonNullable<VikingBotMessageListProps['labels']>;

/** 消息底部动作区。The footer action area of a message. */
function MessageActions({
  message,
  onMessageAction,
}: {
  message: VikingBotMessage;
  onMessageAction?: (messageId: string, actionKey: string) => void;
}) {
  // 合并后的消息可能带着多条原始动作，逐条渲染并回传各自的 messageId。
  const actions =
    message.actions ??
    (message.action ? [{ messageId: message.id, action: message.action }] : []);
  if (actions.length === 0) return null;
  return (
    <div className="ov-viking-bot__message-actions">
      {actions.map(({ messageId, action }) => (
        <button
          key={`${messageId}:${action.key}`}
          type="button"
          className="ov-viking-bot__message-action"
          disabled={
            Boolean(action.disabledReason) ||
            action.status === 'pending' ||
            action.status === 'success'
          }
          title={action.disabledReason}
          onClick={() => onMessageAction?.(messageId, action.key)}
        >
          {action.label ?? action.key}
        </button>
      ))}
    </div>
  );
}

/** 单条消息。One message. */
export function VikingBotMessageRow({
  message,
  labels,
  onOpenUri,
  onCopy,
  onMessageAction,
  renderMessage,
  showTime,
  locale,
}: {
  message: VikingBotMessage;
  labels: Labels;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  onMessageAction?: (messageId: string, actionKey: string) => void;
  renderMessage?: (message: VikingBotMessage) => React.ReactNode;
  showTime?: boolean;
  locale?: string;
}) {
  if (renderMessage) {
    return (
      <div className="ov-viking-bot__message">{renderMessage(message)}</div>
    );
  }

  if (message.role === 'system') {
    return (
      <ErrorCard
        className={
          message.tone === 'error'
            ? 'ov-viking-bot__notice ov-viking-bot__notice--error'
            : 'ov-viking-bot__notice'
        }
        content={messageText(message)}
      />
    );
  }

  const body = (
    <MessageBody message={message} labels={labels} onOpenUri={onOpenUri} />
  );
  const footer = (
    <MessageFooter
      message={message}
      showTime={showTime || message.role === 'user'}
      labels={labels}
      locale={locale}
      onCopy={onCopy}
    />
  );
  return (
    <article
      className={`ov-viking-bot__message ov-viking-bot__message--${message.role}`}
    >
      {message.role === 'user' ? (
        <Bubble
          className="ov-viking-bot__user-bubble"
          placement="end"
          variant="filled"
          maxHeight="9999px"
          footer={footer}
        >
          {body}
        </Bubble>
      ) : message.streaming && message.parts.length === 0 ? (
        <Bubble loading placement="start" variant="text" />
      ) : (
        <>
          {body}
          {footer}
        </>
      )}
      <MessageActions message={message} onMessageAction={onMessageAction} />
    </article>
  );
}

/** 助手仍在生成时的单元。The in-flight assistant unit. */
export function VikingBotStreamingRow({
  streaming,
  labels,
  onOpenUri,
}: {
  streaming: VikingBotStreamingState;
  labels: Labels;
  onOpenUri?: (uri: string) => void;
}) {
  if (streaming.parts.length === 0) {
    return (
      <Bubble
        className="ov-viking-bot__message ov-viking-bot__message--assistant"
        placement="start"
        variant="text"
        loading
      />
    );
  }
  return (
    <VikingBotMessageRow
      message={{
        id: '__streaming__',
        role: 'assistant',
        parts: streaming.parts,
        streaming: true,
      }}
      labels={labels}
      onOpenUri={onOpenUri}
    />
  );
}
