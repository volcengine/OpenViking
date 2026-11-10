import { MessageActivity } from './message-activity.js';
import { MessageMarkdown } from './message-markdown.js';
import { groupByActivity } from './message-groups.js';
import type { VikingBotMessage, VikingBotMessagePart } from './model.js';
import { renderPart } from './render-part.js';
import { mergeToolResults } from './ark-adapter.js';
import { ToolResultLinks } from './tool-result-links.js';
import type { VikingBotMessageListProps } from './types.js';

type Labels = NonNullable<VikingBotMessageListProps['labels']>;

/** Keep activity blocks and answer segments in their original arrival order. */
export function MessageBody({
  message,
  labels,
  onOpenUri,
}: {
  message: VikingBotMessage;
  labels: Labels;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <>
      {groupByActivity(mergeToolResults(message.parts)).map((block, index) => {
        const part = block.parts[0];
        if (!part) return null;
        if (part.type === 'reasoning' || part.type === 'tool')
          return (
            <MessageActivity
              key={`${block.groupId ?? 'activity'}:${index}`}
              parts={block.parts}
              labels={labels}
              onOpenUri={onOpenUri}
            />
          );
        return block.parts.map((value: VikingBotMessagePart, offset) =>
          value.type === 'text' ? (
            value.format === 'plain' ? (
              <div key={`${index}:${offset}`} className="ov-viking-bot__text">
                {value.text}
              </div>
            ) : (
              <MessageMarkdown
                key={`${index}:${offset}`}
                text={value.text}
                streaming={message.streaming}
                user={message.role === 'user'}
                labels={labels}
                onOpenUri={onOpenUri}
              />
            )
          ) : (
            <div key={`${index}:${offset}`}>
              {value.type === 'tool_result' && value.results?.length ? (
                <ToolResultLinks
                  results={value.results}
                  labels={labels}
                  onOpenUri={onOpenUri}
                />
              ) : (
                renderPart(value, { labels, onOpenUri })
              )}
            </div>
          ),
        );
      })}
    </>
  );
}
