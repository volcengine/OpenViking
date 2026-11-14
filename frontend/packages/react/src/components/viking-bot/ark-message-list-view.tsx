import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { ChatMessageList } from '@ark-design/ui/message-list';
import type { Locale } from '../../shared/locale.js';

import { toRenderItems, type VikingBotRenderItem } from './ark-adapter.js';
import { groupMessages } from './message-groups.js';
import {
  VikingBotMessageRow,
  VikingBotStreamingRow,
} from './ark-message-row.js';
import { resolveVikingBotLabels } from './labels.js';
import type { VikingBotMessageListProps } from './types.js';

const INITIAL_TARGET = { kind: 'end', behavior: 'auto' } as const;
const SCROLLER_STYLE = { paddingLeft: 16, paddingRight: 16, paddingBottom: 48 };
const TURN_ANCHOR = { behavior: 'smooth', mode: 'bottom' } as const;
const AT_END_THRESHOLD = 8;

const estimateSize = (item: VikingBotRenderItem) => {
  if (item.streaming) return 96;
  const message = item.message;
  if (!message) return 96;
  if (message.role === 'system') return 56;
  const textLength = message.parts.reduce(
    (total, part) => total + (part.type === 'text' ? part.text.length : 0),
    0,
  );
  const activityCount = message.parts.filter(
    (part) => part.type === 'reasoning' || part.type === 'tool',
  ).length;
  return Math.max(
    72,
    Math.min(640, 72 + textLength * 0.4 + activityCount * 48),
  );
};

/**
 * Ark 渲染的消息列表。只在懒加载边界之后加载，因此可以静态引用 Ark。
 * The Ark-rendered message list. Loaded behind the lazy boundary, so it may
 * import Ark statically.
 */
export function VikingBotMessageListView({
  messages,
  className,
  streaming,
  footer,
  listKey,
  onOpenUri,
  onCopy,
  onMessageAction,
  renderMessage,
  locale,
  labels: overrides,
}: VikingBotMessageListProps & { locale: Locale }) {
  const labels = resolveVikingBotLabels(locale, overrides);
  const errors = useRenderErrors(locale);
  // 同一轮的连续 assistant 消息先合并，列表才按轮次呈现而不是逐条堆叠。
  // 边界必须在原始消息序列上确定：先按 insertIndex 切分，两侧分别合并，
  // 否则先整体合并会让下标含义漂移，把边界两侧的助手消息并到一起。
  const items = toRenderItems(messages, streaming, groupMessages);
  const isStreaming = Boolean(
    streaming || messages.some((message) => message.streaming),
  );
  let lastUser: VikingBotRenderItem | undefined;
  const timed = new Set<string>();
  let lastAssistant: string | undefined;
  for (const item of items) {
    if (item.message?.role === 'user') {
      lastUser = item;
      if (lastAssistant) timed.add(lastAssistant);
      lastAssistant = undefined;
    } else if (
      item.message?.role === 'assistant' &&
      item.message.parts.some((part) => part.type === 'text')
    ) {
      lastAssistant = item.id;
    }
  }
  if (lastAssistant) timed.add(lastAssistant);

  return (
    <div
      className={['ov-viking-bot__list', className].filter(Boolean).join(' ')}
    >
      <ChatMessageList<VikingBotRenderItem, string>
        listKey={listKey ?? 'viking-bot'}
        items={items}
        getItemKey={(item) => item.id}
        estimateItemSize={estimateSize}
        initialTarget={INITIAL_TARGET}
        followOutput={isStreaming ? 'smooth' : false}
        run={
          lastUser
            ? {
                anchorItemKey: lastUser.id,
                runKey: lastUser.id,
                status: isStreaming ? 'running' : 'complete',
              }
            : null
        }
        scrollToLatestButton={false}
        turnAnchor={TURN_ANCHOR}
        atEndThreshold={AT_END_THRESHOLD}
        scrollerStyle={SCROLLER_STYLE}
        shortContentAlign="start"
        overscan={600}
        renderItem={({ item }) => (
          <ErrorBoundary
            message={errors.message}
            locale={locale}
            resetKeys={[listKey, item.id]}
          >
            {item.streaming ? (
              <VikingBotStreamingRow
                streaming={item.streaming}
                labels={labels}
                onOpenUri={onOpenUri}
              />
            ) : item.message ? (
              <VikingBotMessageRow
                message={item.message}
                labels={labels}
                onOpenUri={onOpenUri}
                onCopy={onCopy}
                onMessageAction={onMessageAction}
                renderMessage={renderMessage}
                showTime={timed.has(item.id)}
                locale={locale}
              />
            ) : null}
          </ErrorBoundary>
        )}
      />
      {footer ? <div className="ov-viking-bot__footer">{footer}</div> : null}
    </div>
  );
}
