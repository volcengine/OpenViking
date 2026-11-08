import { ActivityTimeline } from '@ark-design/ui/activity-timeline';
import { MDRenderer } from '@ark-design/ui/markdown-renderer';

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

/** 执行块：推理与工具片段按 groupId 聚合成 Ark 时间线。 */
function ActivityBlock({
  parts,
  onOpenUri,
}: {
  parts: readonly VikingBotMessagePart[];
  onOpenUri?: (uri: string) => void;
}) {
  const { groups, order } = groupActivityParts(parts);
  if (order.length === 0) return null;
  return (
    <div className="ov-viking-bot__activity">
      {order.map((key) => {
        const group = groups.get(key);
        if (!group) return null;
        return (
          <ActivityTimeline
            key={group.id}
            items={group.items}
            density="compact"
            defaultCollapseCompleted
            onOpenSearchResult={(result) => onOpenUri?.(result.url)}
          />
        );
      })}
    </div>
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

  return (
    <>
      {activity.length > 0 ? (
        <ActivityBlock parts={activity} onOpenUri={onOpenUri} />
      ) : null}
      {rest.map((part, index) =>
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
