import { useState } from 'react';

import { DEFAULT_LABELS } from './labels.js';
import { groupByActivity, messageText } from './message-groups.js';
import type {
  VikingBotMessage,
  VikingBotMessagePart,
  VikingBotStreamingState,
} from './model.js';
import { renderPart } from './render-part.js';
import type { VikingBotMessageListProps } from './types.js';

function MessageRow({
  message,
  labels,
  onOpenUri,
  onCopy,
  onMessageAction,
  renderMessage,
}: {
  message: VikingBotMessage;
  labels: NonNullable<VikingBotMessageListProps['labels']>;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  onMessageAction?: (messageId: string, actionKey: string) => void;
  renderMessage?: (message: VikingBotMessage) => React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const text = messageText(message);

  if (renderMessage) {
    return (
      <div className="ov-viking-bot__message">{renderMessage(message)}</div>
    );
  }

  return (
    <article
      className={`ov-viking-bot__message ov-viking-bot__message--${message.role}`}
    >
      {message.role === 'system' ? (
        <p
          className="ov-viking-bot__notice"
          role={message.tone === 'error' ? 'alert' : 'status'}
        >
          {text}
        </p>
      ) : (
        groupByActivity(message.parts).map((block, index) => (
          <div key={index} className="ov-viking-bot__block">
            {block.parts.map((part, partIndex) => (
              <div key={partIndex}>
                {renderPart(part, { labels, onOpenUri })}
              </div>
            ))}
          </div>
        ))
      )}
      {message.action ? (
        <button
          type="button"
          className="ov-viking-bot__message-action"
          disabled={
            message.action.status === 'pending' ||
            message.action.status === 'success' ||
            Boolean(message.action.disabledReason)
          }
          title={message.action.disabledReason}
          onClick={() => onMessageAction?.(message.id, message.action!.key)}
        >
          {message.action.label ?? message.action.key}
        </button>
      ) : null}
      {message.role !== 'system' && text && onCopy ? (
        <button
          type="button"
          className="ov-viking-bot__copy"
          onClick={() => {
            onCopy(text);
            setCopied(true);
          }}
        >
          {copied ? labels.copied : labels.copy}
        </button>
      ) : null}
    </article>
  );
}

function StreamingRow({
  streaming,
  labels,
  onOpenUri,
}: {
  streaming: VikingBotStreamingState;
  labels: NonNullable<VikingBotMessageListProps['labels']>;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <article
      className="ov-viking-bot__message ov-viking-bot__message--assistant"
      aria-busy
    >
      {streaming.parts.length === 0 ? (
        <p className="ov-viking-bot__thinking" role="status">
          {labels.thinking}
        </p>
      ) : (
        streaming.parts.map((part: VikingBotMessagePart, index: number) => (
          <div key={index}>{renderPart(part, { labels, onOpenUri })}</div>
        ))
      )}
    </article>
  );
}

/** 占位组件：仅按 props 契约渲染可用视图，未经设计评审，真实实现待重写。 */
export function VikingBotMessageList({
  messages,
  className,
  streaming,
  footer,
  listKey,
  onOpenUri,
  onCopy,
  onMessageAction,
  renderMessage,
  labels: overrides,
}: VikingBotMessageListProps) {
  const labels = { ...DEFAULT_LABELS, ...overrides };
  const insertIndex = streaming
    ? Math.min(streaming.insertIndex ?? messages.length, messages.length)
    : messages.length;
  const before = messages.slice(0, insertIndex);
  const after = messages.slice(insertIndex);

  return (
    <div
      key={listKey}
      className={['ov-viking-bot__list', className].filter(Boolean).join(' ')}
      role="log"
      aria-live="polite"
      aria-busy={streaming ? true : undefined}
    >
      {before.map((message) => (
        <MessageRow
          key={message.id}
          message={message}
          labels={labels}
          onOpenUri={onOpenUri}
          onCopy={onCopy}
          onMessageAction={onMessageAction}
          renderMessage={renderMessage}
        />
      ))}
      {streaming ? (
        <StreamingRow
          streaming={streaming}
          labels={labels}
          onOpenUri={onOpenUri}
        />
      ) : null}
      {after.map((message) => (
        <MessageRow
          key={message.id}
          message={message}
          labels={labels}
          onOpenUri={onOpenUri}
          onCopy={onCopy}
          onMessageAction={onMessageAction}
          renderMessage={renderMessage}
        />
      ))}
      {footer ? <div className="ov-viking-bot__footer">{footer}</div> : null}
    </div>
  );
}
