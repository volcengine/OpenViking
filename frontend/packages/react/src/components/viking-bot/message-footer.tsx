import { CopyAction } from './copy-action.js';
import { messageText } from './message-groups.js';
import type { VikingBotLabels, VikingBotMessage } from './model.js';

export function MessageFooter({
  message,
  showTime,
  debugText,
  labels,
  locale,
  onCopy,
}: {
  message: VikingBotMessage;
  showTime?: boolean;
  debugText?: () => string;
  labels: VikingBotLabels;
  locale?: string;
  onCopy?: (text: string) => void;
}) {
  const text = messageText(message);
  const date = message.createdAt ? new Date(message.createdAt) : undefined;
  const time =
    showTime && date && !Number.isNaN(date.getTime())
      ? date.toLocaleString(locale, {
          day: '2-digit',
          hour: '2-digit',
          hour12: false,
          minute: '2-digit',
          month: '2-digit',
          year: 'numeric',
        })
      : '';
  const canCopy = Boolean(text && (message.role === 'user' || onCopy));
  if (!time && !canCopy) return null;
  return (
    <div className="ov-viking-bot__message-footer">
      {time ? (
        debugText ? (
          <CopyAction
            className="ov-viking-bot__message-time"
            text={debugText}
            labels={{
              ...labels,
              copy:
                message.role === 'user'
                  ? labels.copySessionDebug
                  : labels.copyMessageDebug,
              copied: labels.debugCopied,
            }}
            failureLabel={labels.debugCopyFailed}
            onCopy={onCopy}
            icon={() => <time dateTime={message.createdAt}>{time}</time>}
          />
        ) : (
          <time dateTime={message.createdAt}>{time}</time>
        )
      ) : null}
      {canCopy ? (
        <CopyAction
          className="ov-viking-bot__copy"
          text={text}
          labels={labels}
          onCopy={onCopy}
        />
      ) : null}
    </div>
  );
}
