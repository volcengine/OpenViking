import { useCallback } from 'react';
import type { SessionPreviewMessage } from './session-messages.js';

function createdTime(value: string, locale: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? '';
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`;
}

export function SessionMessagesView({
  messages,
  locale,
}: {
  messages: readonly SessionPreviewMessage[];
  locale: string;
}) {
  const showLatest = useCallback((node: HTMLDivElement | null) => {
    if (node) node.scrollTop = node.scrollHeight;
  }, []);
  return (
    <div className="ov-file-preview__session" ref={showLatest}>
      {messages.map((message, index) => (
        <article
          key={`${index}:${message.id}`}
          className={`ov-file-preview__session-message ov-file-preview__session-message--${
            ['user', 'tool', 'system'].includes(message.role)
              ? message.role
              : 'assistant'
          }`}
        >
          <div className="ov-file-preview__session-role">{message.role}</div>
          <div className="ov-file-preview__session-bubble">
            <div className="ov-file-preview__session-content">
              {message.content}
            </div>
            <div className="ov-file-preview__session-meta">
              {message.createdAt ? (
                <time dateTime={message.createdAt}>
                  {createdTime(message.createdAt, locale)}
                </time>
              ) : null}
              <span>{message.id}</span>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
