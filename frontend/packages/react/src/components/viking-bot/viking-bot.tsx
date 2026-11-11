import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import { useRef } from 'react';
import { resolveVikingBotLabels } from './labels.js';
import { VikingBotSessionActions } from './session-actions.js';
import { VikingBotWelcome } from './welcome.js';
import { QuickInputScope, useQuickInput } from './use-quick-input.js';
import type { VikingBotQuickInput } from './capabilities.js';
import type { VikingBotComposerRef } from './types.js';
import type { VikingBotProps } from './types.js';
import { VikingBotComposer } from './lazy-composer.js';
import { VikingBotInteraction } from './interaction/index.js';
import { VikingBotMessageList } from './viking-bot-message-list.js';

/**
 * 完整对话区。消息列表、输入区与待确认交互都可被宿主替换：
 * 传 `messageList`、`composer` 或 `interaction` 时使用宿主的渲染底座，
 * 不传时使用内置实现。
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
  sessions,
  headerActions,
  locale: localeProp,
  quickInputs,
  onQuickInputSelect,
  references,
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
  const locale = useLocale(localeProp);
  const labels = resolveVikingBotLabels(locale, overrides);
  const hasMessages = messages.length > 0;
  const localComposer = useRef<VikingBotComposerRef>(null);
  const inputRef = composerRef ?? localComposer;
  const shortcuts = useQuickInput(
    draft ?? '',
    (next) => {
      if (inputRef.current) inputRef.current.setText(next);
      else onDraftChange?.(next);
    },
    onQuickInputSelect,
    true,
  );
  const selectQuickInput = (item: VikingBotQuickInput) => {
    shortcuts.select(item);
    inputRef.current?.focus();
  };
  const renderedHeader =
    header !== undefined ? (
      header
    ) : sessions || headerActions ? (
      <VikingBotSessionActions
        sessions={sessions}
        extra={headerActions}
        labels={labels}
        locale={locale}
      />
    ) : null;

  if (children) {
    return (
      <div
        className={['ov-viking-bot', className].filter(Boolean).join(' ')}
        {...rest}
      >
        <LocaleProvider locale={locale}>{children}</LocaleProvider>
      </div>
    );
  }

  return (
    <QuickInputScope.Provider value={shortcuts.tracker}>
      <LocaleProvider locale={locale}>
        <div
          className={['ov-viking-bot', className].filter(Boolean).join(' ')}
          {...rest}
        >
          {renderedHeader ? (
            <div className="ov-viking-bot__header">{renderedHeader}</div>
          ) : null}
          {!isAvailable ? (
            <p className="ov-viking-bot__unavailable" role="status">
              {unavailableMessage ?? labels.unavailable}
            </p>
          ) : null}
          <div className="ov-viking-bot__body">
            {messageList !== undefined ? (
              messageList
            ) : !hasMessages && !streaming ? (
              <div
                className={`ov-viking-bot__empty${emptyState === undefined ? ' ov-viking-bot__empty--welcome' : ''}`}
              >
                {emptyState === undefined ? (
                  <VikingBotWelcome
                    labels={labels}
                    quickInputs={quickInputs}
                    onSelect={selectQuickInput}
                    disabled={
                      isSending ||
                      !isAvailable ||
                      (!onDraftChange && !onQuickInputSelect)
                    }
                  />
                ) : (
                  emptyState
                )}
              </div>
            ) : (
              <VikingBotMessageList
                locale={locale}
                messages={messages}
                streaming={streaming}
                listKey={listKey}
                onOpenUri={onOpenUri}
                onCopy={onCopy}
                onMessageAction={onMessageAction}
                labels={overrides}
              />
            )}
          </div>
          {footer ? (
            <div className="ov-viking-bot__footer">{footer}</div>
          ) : null}
          {interaction
            ? (interactionSlot ?? (
                <VikingBotInteraction
                  {...interaction}
                  locale={interaction.locale ?? locale}
                />
              ))
            : (composer ??
              // 省略 draft 表示只读会话，此时不渲染默认输入区。
              (draft !== undefined ? (
                <VikingBotComposer
                  locale={locale}
                  ref={inputRef}
                  value={draft}
                  attachments={attachments}
                  disabled={!isAvailable}
                  isSending={isSending}
                  references={references}
                  quickInputs={
                    hasMessages || streaming || emptyState !== undefined
                      ? quickInputs
                      : undefined
                  }
                  onQuickInputSelect={selectQuickInput}
                  onValueChange={
                    onDraftChange
                      ? (next) => {
                          shortcuts.tracker.edit(next);
                          onDraftChange(next);
                        }
                      : undefined
                  }
                  onFilesAdded={onFilesAdded}
                  onAttachmentRemove={onAttachmentRemove}
                  onOpenUri={onOpenUri}
                  onSend={onSend}
                  onCancel={onCancel}
                  labels={overrides}
                />
              ) : null))}
        </div>
      </LocaleProvider>
    </QuickInputScope.Provider>
  );
}
