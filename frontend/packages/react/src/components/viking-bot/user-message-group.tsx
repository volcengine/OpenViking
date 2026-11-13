import { Fragment, type ReactNode } from 'react';
import { Bubble } from '@ark-design/ui/bubble';
import { UserMessageFileList } from '@ark-design/ui/user-message-file-list';

import { MessageBody } from './ark-message-body.js';
import type { VikingBotMessage } from './model.js';
import { renderPart } from './render-part.js';
import type { VikingBotMessageListProps } from './types.js';

export function UserMessageGroup({
  message,
  labels,
  footer,
  onOpenUri,
}: {
  message: VikingBotMessage;
  labels: NonNullable<VikingBotMessageListProps['labels']>;
  footer: ReactNode;
  onOpenUri?: (uri: string) => void;
}) {
  const attachments = message.parts.filter(
    (part) => part.type === 'attachment',
  );
  const parts = message.parts.filter((part) => part.type !== 'attachment');
  const hasContent = parts.some(
    (part) => part.type !== 'text' || Boolean(part.text),
  );
  if (!hasContent && attachments.length === 0) return null;

  return (
    <div className="ov-viking-bot__user-message-group">
      {attachments.length > 0 ? (
        <UserMessageFileList
          className="ov-viking-bot__user-message-attachments"
          items={attachments.map((part, index) => ({
            id: part.uri ?? String(index),
            title: part.name,
            previewEnabled: false,
          }))}
          renderItem={(_item, index) => (
            <Fragment key={index}>
              {renderPart(attachments[index]!, { labels, onOpenUri })}
            </Fragment>
          )}
        />
      ) : null}
      {hasContent ? (
        <Bubble
          className="ov-viking-bot__user-bubble"
          placement="end"
          variant="filled"
          maxHeight="9999px"
          footer={footer}
        >
          <div className="ov-viking-bot__user-message-content">
            <MessageBody
              message={{ ...message, parts }}
              labels={labels}
              onOpenUri={onOpenUri}
            />
          </div>
        </Bubble>
      ) : (
        footer
      )}
    </div>
  );
}
