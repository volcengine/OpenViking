import { ActivityTimeline } from '@ark-design/ui/activity-timeline';
import { MDRenderer } from '@ark-design/ui/markdown-renderer';
import { useState } from 'react';

import { groupActivityParts } from './ark-adapter.js';
import type { VikingBotMessage, VikingBotMessagePart } from './model.js';
import { renderPart } from './render-part.js';
import type { VikingBotMessageListProps } from './types.js';

type Labels = NonNullable<VikingBotMessageListProps['labels']>;

/** Markdown 或纯文本正文。A Markdown or plain-text body. */
export function MessageText({
  text,
  streaming,
  format,
}: {
  text: string;
  streaming?: boolean;
  format?: 'plain' | 'markdown';
}) {
  if (format === 'plain') {
    return <div className="ov-viking-bot__text">{text}</div>;
  }
  return (
    <MDRenderer
      className="ov-viking-bot__text ov-viking-bot__text--markdown"
      markdown={text}
      showIndicator={streaming}
    />
  );
}

/** 一个执行块：整组共用一个折叠入口，而不是每个片段各自折叠。 */
function ActivityGroup({
  id,
  items,
  running,
  labels,
  onOpenUri,
}: {
  id: string;
  items: Parameters<typeof ActivityTimeline>[0]['items'];
  running: boolean;
  labels: Labels;
  onOpenUri?: (uri: string) => void;
}) {
  const [expanded, setExpanded] = useState(running);
  const duration = items.reduce(
    (total, item) => total + (item.durationMs ?? 0),
    0,
  );
  const summary = running
    ? labels.thinking
    : duration > 0
      ? `${labels.reasoning} · ${Math.round(duration / 1000)}s`
      : labels.reasoning;

  return (
    <section className="ov-viking-bot__activity" data-group={id}>
      <button
        type="button"
        className="ov-viking-bot__activity-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        {summary}
      </button>
      {expanded ? (
        <ActivityTimeline
          items={items}
          density="compact"
          defaultExpanded
          onOpenSearchResult={(result) => onOpenUri?.(result.url)}
        />
      ) : null}
    </section>
  );
}

/** 消息正文：执行块在前，其余片段按顺序渲染。 */
export function MessageBody({
  message,
  labels,
  onOpenUri,
}: {
  message: VikingBotMessage;
  labels: Labels;
  onOpenUri?: (uri: string) => void;
}) {
  const activity = message.parts.filter(
    (part) => part.type === 'reasoning' || part.type === 'tool',
  );
  const rest = message.parts.filter(
    (part) => part.type !== 'reasoning' && part.type !== 'tool',
  );
  const { groups, order } = groupActivityParts(activity);

  return (
    <>
      {order.map((key) => {
        const group = groups.get(key);
        if (!group) return null;
        return (
          <ActivityGroup
            key={group.id}
            id={group.id}
            items={group.items}
            running={group.items.some((item) => item.status === 'running')}
            labels={labels}
            onOpenUri={onOpenUri}
          />
        );
      })}
      {rest.map((part: VikingBotMessagePart, index: number) =>
        part.type === 'text' ? (
          <MessageText
            key={index}
            text={part.text}
            format={part.format}
            streaming={message.streaming}
          />
        ) : (
          <div key={index}>{renderPart(part, { labels, onOpenUri })}</div>
        ),
      )}
    </>
  );
}
