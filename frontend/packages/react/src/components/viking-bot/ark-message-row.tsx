import { Bubble } from '@ark-design/ui/bubble';
import { ErrorCard } from '@ark-design/ui/error-card';

import { MessageBody } from './ark-message-body.js';
import { messageText } from './message-groups.js';
import type { VikingBotMessage, VikingBotStreamingState } from './model.js';
import type { VikingBotMessageListProps } from './types.js';

type Labels = NonNullable<VikingBotMessageListProps['labels']>;

/** 消息底部动作区。The footer action area of a message. */
function MessageActions({
  message,
  labels,
  onCopy,
  onMessageAction,
}: {
  message: VikingBotMessage;
  labels: Labels;
  onCopy?: (text: string) => void;
  onMessageAction?: (messageId: string, actionKey: string) => void;
}) {
  const text = messageText(message);
  // 合并后的消息可能带着多条原始动作，逐条渲染并回传各自的 messageId。
  const actions =
    message.actions ??
    (message.action ? [{ messageId: message.id, action: message.action }] : []);
  if (actions.length === 0 && !onCopy) return null;
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
      {onCopy && text ? (
        <button
          type="button"
          className="ov-viking-bot__copy"
          onClick={() => onCopy(text)}
        >
          {labels.copy}
        </button>
      ) : null}
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
}: {
  message: VikingBotMessage;
  labels: Labels;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  onMessageAction?: (messageId: string, actionKey: string) => void;
  renderMessage?: (message: VikingBotMessage) => React.ReactNode;
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

  return (
    <Bubble
      className={`ov-viking-bot__message ov-viking-bot__message--${message.role}`}
      placement={message.role === 'user' ? 'end' : 'start'}
      variant={message.role === 'user' ? 'filled' : 'text'}
      content={null}
      footer={
        <MessageActions
          message={message}
          labels={labels}
          onCopy={onCopy}
          onMessageAction={onMessageAction}
        />
      }
    >
      <MessageBody message={message} labels={labels} onOpenUri={onOpenUri} />
    </Bubble>
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
    <Bubble
      className="ov-viking-bot__message ov-viking-bot__message--assistant"
      placement="start"
      variant="text"
      content={null}
    >
      <MessageBody
        message={{
          id: '__streaming__',
          role: 'assistant',
          parts: streaming.parts,
          streaming: true,
        }}
        labels={labels}
        onOpenUri={onOpenUri}
      />
    </Bubble>
  );
}
