import { Sender } from '@ark-design/ui/sender';
import type { RichTextValue, SenderInstance } from '@ark-design/ui/sender';
import { forwardRef, useCallback, useImperativeHandle, useRef } from 'react';

import { DEFAULT_LABELS } from './labels.js';
import type { VikingBotDraft } from './model.js';
import type { VikingBotComposerProps, VikingBotComposerRef } from './types.js';

/** 从富文本里取纯文本，忽略附件与标签。 */
const toPlainText = (content: RichTextValue[]): string =>
  content
    .map((item) => {
      if (item.type === 'hardBreak') return '\n';
      return item.type === 'text' ? (item.text ?? '') : '';
    })
    .join('')
    .trim();

const toRichText = (text: string): RichTextValue[] =>
  text ? [{ type: 'text', text }] : [];

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
    className,
    attachments = [],
    disabled,
    disabledReason,
    isSending,
    variant = 'default',
    actions,
    onValueChange,
    onFilesAdded,
    onAttachmentRemove,
    onSend,
    onCancel,
    labels: overrides,
  },
  ref,
) {
  const labels = { ...DEFAULT_LABELS, ...overrides };
  const senderRef = useRef<SenderInstance>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const readOnly = !onValueChange;

  const setText = useCallback(
    (text: string) => {
      senderRef.current?.setValue(toRichText(text));
      onValueChange?.(text);
    },
    [onValueChange],
  );

  useImperativeHandle(ref, () => ({
    setText,
    appendText: (text: string) => setText(`${value}${text}`),
    addFiles: (files: readonly File[]) => onFilesAdded?.(files),
    isAttachmentLimitReached: () => false,
    clear: () => setText(''),
    focus: () => senderRef.current?.getEditor()?.commands.focus('end'),
  }));

  const submit = () => {
    if (!onSend || disabled || isSending) return;
    const text = toPlainText(
      senderRef.current?.getValue() ?? toRichText(value),
    );
    onSend({ text, attachments } satisfies VikingBotDraft);
  };

  const acceptDrop = Boolean(onFilesAdded) && !disabled;

  return (
    <div
      className={[
        'ov-viking-bot__composer',
        `ov-viking-bot__composer--${variant}`,
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      onDragOver={(event) => {
        if (!acceptDrop) return;
        // 阻止默认行为，否则浏览器会直接打开被拖入的文件。
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
      }}
      onDrop={(event) => {
        if (!acceptDrop) return;
        event.preventDefault();
        const files = [...(event.dataTransfer.files ?? [])];
        if (files.length) onFilesAdded?.(files);
      }}
    >
      {disabled && disabledReason ? (
        <p className="ov-viking-bot__composer-hint" role="status">
          {disabledReason}
        </p>
      ) : null}
      {attachments.length > 0 ? (
        <ul className="ov-viking-bot__attachments">
          {attachments.map((attachment) => (
            <li key={attachment.key}>
              <span>{attachment.name}</span>
              {attachment.status === 'error' ? (
                <span className="ov-viking-bot__attachment-status">
                  {attachment.errorMessage ?? labels.toolError}
                </span>
              ) : null}
              {onAttachmentRemove ? (
                <button
                  type="button"
                  aria-label={labels.removeAttachment}
                  disabled={disabled}
                  onClick={() => onAttachmentRemove(attachment)}
                >
                  ×
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <Sender
        ref={senderRef}
        className="ov-viking-bot__sender"
        defaultContent={toRichText(value)}
        sending={isSending}
        sendDisabled={disabled || readOnly}
        onContentChange={(content) => {
          if (!readOnly) onValueChange?.(toPlainText(content));
        }}
        onSend={() => {
          submit();
        }}
        onStop={() => onCancel?.()}
        richTextAreaProps={{
          placeholder: labels.placeholder,
          disabled,
        }}
        renderFooter={({ components }) => (
          <div className="ov-viking-bot__composer-actions">
            {components.sendBtnComponent}
            {onFilesAdded ? (
              <>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  hidden
                  onChange={(event) => {
                    const files = [...(event.target.files ?? [])];
                    if (files.length) onFilesAdded(files);
                    event.target.value = '';
                  }}
                />
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => fileRef.current?.click()}
                >
                  {labels.attach}
                </button>
              </>
            ) : null}
            {actions}
          </div>
        )}
      />
    </div>
  );
});
