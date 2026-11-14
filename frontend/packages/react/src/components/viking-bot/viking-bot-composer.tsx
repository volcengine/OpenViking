import { useLocale } from '../../shared/locale-context.js';
import { Sender } from '@ark-design/ui/sender';
import type { SenderInstance } from '@ark-design/ui/sender';
import { forwardRef, useCallback, useImperativeHandle, useRef } from 'react';

import { resolveVikingBotLabels } from './labels.js';
import { VikingBotSubmitActions } from './composer-controls.js';
import { composerEvents } from './composer-events.js';
import { ComposerHeader } from './composer-header.js';
import { attachmentsAreReady } from './composer-attachments.js';
import { composerMention } from './composer-references.js';
import { ComposerFooter } from './composer-footer.js';
import { QuickInputs } from './quick-inputs.js';
import { useQuickInput } from './use-quick-input.js';
import {
  composerCaret,
  toPlainText,
  toRichText,
  useSyncComposerValue,
  writeComposerText,
} from './composer-text.js';
import type { VikingBotComposerProps, VikingBotComposerRef } from './types.js';

/**
 * 输入区。发送与停止都由宿主决定，组件只上报意图。
 * The composer. The host owns sending and stopping; the component reports intent.
 */
export const VikingBotComposerView = forwardRef<
  VikingBotComposerRef,
  VikingBotComposerProps
>(function VikingBotComposerView(
  {
    value,
    locale: localeProp,
    className,
    attachments = [],
    disabled,
    disabledReason,
    isSending,
    canSendWhileStreaming = false,
    isSubmitting,
    variant = 'default',
    accept,
    maxAttachments = 10,
    actions,
    quickInputs,
    onQuickInputSelect,
    references,
    onValueChange,
    onFilesAdded,
    onAttachmentRemove,
    onOpenUri,
    onSend,
    onCancel,
    labels: overrides,
  },
  ref,
) {
  const locale = useLocale(localeProp);
  const labels = resolveVikingBotLabels(locale, overrides);
  const senderRef = useRef<SenderInstance>(null);
  const writing = useRef(false);
  const locked = Boolean(
    disabled ||
    !onValueChange ||
    isSubmitting ||
    (isSending && !canSendWhileStreaming),
  );
  const canSubmit =
    (value.trim() || attachments.length || references?.selected.length) &&
    attachmentsAreReady(attachments);
  const isSubmitMode = !isSending || canSendWhileStreaming;
  const remaining = Math.max(0, maxAttachments - attachments.length);

  const setText = useCallback(
    (text: string) => {
      writeComposerText(senderRef.current, text, writing);
      onValueChange?.(text);
    },
    [onValueChange],
  );
  const shortcuts = useQuickInput(value, setText, onQuickInputSelect);

  useSyncComposerValue(senderRef, value, writing);

  /** 按剩余额度截断并上报文件。 */
  const reportFiles = useCallback(
    (files: readonly File[]) => {
      if (!locked && files.length && remaining)
        onFilesAdded?.(files.slice(0, remaining));
    },
    [locked, onFilesAdded, remaining],
  );

  useImperativeHandle(ref, () => ({
    setText,
    appendText: (text: string) => setText(`${value}${text}`),
    addFiles: (files: readonly File[]) => reportFiles(files),
    isAttachmentLimitReached: () => remaining === 0,
    clear: () => setText(''),
    focus: () => senderRef.current?.getEditor()?.commands.focus('end'),
  }));

  const submit = () => {
    if (!onSend || locked || !canSubmit) return;
    const text = toPlainText(
      senderRef.current?.getValue() ?? toRichText(value),
    ).trim();
    onSend({
      text,
      attachments,
      ...(references ? { references: references.selected } : {}),
    });
  };

  return (
    <div
      className={[
        'ov-viking-bot__composer',
        `ov-viking-bot__composer--${variant}`,
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      {...composerEvents(
        Boolean(onFilesAdded),
        !locked && remaining > 0,
        reportFiles,
        senderRef,
      )}
    >
      {disabled && disabledReason ? (
        <p className="ov-viking-bot__composer-hint" role="status">
          {disabledReason}
        </p>
      ) : null}
      <QuickInputs
        items={quickInputs}
        disabled={locked}
        onSelect={(item) => {
          shortcuts.select(item);
          senderRef.current?.getEditor()?.commands.focus('end');
        }}
      />
      <Sender
        ref={senderRef}
        className="ov-viking-bot__sender"
        defaultContent={toRichText(value)}
        sending={isSending && !canSendWhileStreaming}
        sendDisabled={isSubmitMode ? locked || !canSubmit || !onSend : false}
        onContentChange={(content) => {
          if (!onValueChange || writing.current) return;
          const next = toPlainText(content);
          shortcuts.tracker.edit(next, composerCaret(senderRef.current));
          onValueChange?.(next);
        }}
        onSend={submit}
        onStop={() => onCancel?.()}
        renderHeader={() => (
          <ComposerHeader
            references={references}
            labels={labels}
            onOpenUri={onOpenUri}
            attachments={attachments}
            onAttachmentRemove={onAttachmentRemove}
            disabled={locked}
          />
        )}
        renderSubmit={(props) => (
          <VikingBotSubmitActions
            props={props}
            streaming={isSending}
            canSteer={canSendWhileStreaming}
            onCancel={onCancel}
            labels={labels}
          />
        )}
        richTextAreaProps={{
          mentionConfig: composerMention(references, locked, labels, locale),
          placeholder: labels.placeholder,
          disabled,
          minRows: 1,
          maxRows: 6,
          style: { minHeight: 44 },
        }}
        renderFooter={({ components }) => (
          <ComposerFooter
            locale={locale}
            references={references}
            senderRef={senderRef}
            labels={labels}
            disabled={locked}
            filesDisabled={Boolean(locked || remaining === 0)}
            accept={accept}
            onFiles={onFilesAdded ? reportFiles : undefined}
            actions={actions}
            submit={components.sendBtnComponent}
          />
        )}
      />
    </div>
  );
});
