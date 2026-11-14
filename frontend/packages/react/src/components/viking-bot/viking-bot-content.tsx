import { VikingBotRegion } from './viking-bot-region.js';
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
export function VikingBotContent({
  messages,
  className,
  streaming,
  draft,
  attachments,
  accept,
  isSending,
  canSendWhileStreaming,
  isSubmitting,
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
  const disabledReason = isAvailable
    ? undefined
    : (unavailableMessage ?? labels.unavailable);
  const hasDefaultComposer =
    !interaction && composer == null && draft !== undefined;
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
    if (!isAvailable) return;
    shortcuts.select(item);
    inputRef.current?.focus();
  };
  const updateDraft = (draft: string) => {
    shortcuts.tracker.edit(draft);
    onDraftChange?.(draft);
  };
  const renderedHeader =
    header !== undefined ? (
      header
    ) : sessions || headerActions ? (
      <VikingBotSessionActions
        sessions={sessions}
        extra={headerActions}
        onOpenUri={onOpenUri}
        onCopy={onCopy}
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
            <VikingBotRegion name="header">{renderedHeader}</VikingBotRegion>
          ) : null}
          {disabledReason && !hasDefaultComposer ? (
            <p className="ov-viking-bot__unavailable" role="status">
              {disabledReason}
            </p>
          ) : null}
          <VikingBotRegion name="body" resetKey={listKey}>
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
          </VikingBotRegion>
          {footer ? (
            <VikingBotRegion name="footer">{footer}</VikingBotRegion>
          ) : null}
          <VikingBotRegion resetKey={listKey}>
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
                    accept={accept}
                    disabled={!isAvailable}
                    disabledReason={disabledReason}
                    isSending={isSending}
                    canSendWhileStreaming={canSendWhileStreaming}
                    isSubmitting={isSubmitting}
                    references={references}
                    quickInputs={
                      hasMessages || streaming || emptyState !== undefined
                        ? quickInputs
                        : undefined
                    }
                    onQuickInputSelect={selectQuickInput}
                    onValueChange={onDraftChange && updateDraft}
                    onFilesAdded={onFilesAdded}
                    onAttachmentRemove={onAttachmentRemove}
                    onOpenUri={onOpenUri}
                    onSend={onSend}
                    onCancel={onCancel}
                    labels={overrides}
                  />
                ) : null))}
          </VikingBotRegion>
        </div>
      </LocaleProvider>
    </QuickInputScope.Provider>
  );
}
