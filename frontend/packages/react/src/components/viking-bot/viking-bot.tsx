import { DEFAULT_LABELS } from './labels.js';
import type { VikingBotProps } from './types.js';
import { VikingBotComposer } from './lazy-composer.js';
import { VikingBotInteraction } from './interaction/index.js';
import { VikingBotMessageList } from './viking-bot-message-list.js';

/**
 * 占位组件：仅按 props 契约渲染可用视图，未经设计评审，真实实现待重写。
 *
 * 完整对话区。消息列表、输入区与待确认交互都可被宿主替换：
 * 传 `messageList`、`composer` 或 `interaction` 时使用宿主的渲染底座，
 * 不传时使用内置实现。
 *
 * Placeholder: renders a working view against the props contract only, pending
 * design review; the real implementation will be rewritten.
 *
 * The whole conversation view. The message list, the composer and the pending
 * interaction can each be replaced by the host; the built-in implementations
 * are used when they are omitted.
 */
export function VikingBot({
  messages,
  className,
  streaming,
  draft,
  attachments,
  isSending,
  isAvailable = true,
  unavailableMessage,
  emptyState,
  header,
  footer,
  children,
  messageList,
  composer,
  interaction,
  interactionSlot,
  listKey,
  composerRef,
  onDraftChange,
  onFilesAdded,
  onAttachmentRemove,
  onSend,
  onCancel,
  onOpenUri,
  onCopy,
  onMessageAction,
  labels: overrides,
  ...rest
}: VikingBotProps) {
  const labels = { ...DEFAULT_LABELS, ...overrides };
  const hasMessages = messages.length > 0;

  if (children) {
    return (
      <div
        className={['ov-viking-bot', className].filter(Boolean).join(' ')}
        {...rest}
      >
        {children}
      </div>
    );
  }

  return (
    <div
      className={['ov-viking-bot', className].filter(Boolean).join(' ')}
      {...rest}
    >
      {header ? <div className="ov-viking-bot__header">{header}</div> : null}
      {!isAvailable ? (
        <p className="ov-viking-bot__unavailable" role="status">
          {unavailableMessage ?? labels.unavailable}
        </p>
      ) : null}
      <div className="ov-viking-bot__body">
        {!hasMessages && emptyState ? (
          <div className="ov-viking-bot__empty">{emptyState}</div>
        ) : (
          (messageList ?? (
            <VikingBotMessageList
              messages={messages}
              streaming={streaming}
              listKey={listKey}
              onOpenUri={onOpenUri}
              onCopy={onCopy}
              onMessageAction={onMessageAction}
              labels={overrides}
            />
          ))
        )}
      </div>
      {footer ? <div className="ov-viking-bot__footer">{footer}</div> : null}
      {interaction
        ? (interactionSlot ?? <VikingBotInteraction {...interaction} />)
        : (composer ??
          // 省略 draft 表示只读会话，此时不渲染默认输入区。
          (draft !== undefined ? (
            <VikingBotComposer
              ref={composerRef}
              value={draft}
              attachments={attachments}
              disabled={!isAvailable}
              isSending={isSending}
              onValueChange={onDraftChange}
              onFilesAdded={onFilesAdded}
              onAttachmentRemove={onAttachmentRemove}
              onSend={onSend}
              onCancel={onCancel}
              labels={overrides}
            />
          ) : null))}
    </div>
  );
}
