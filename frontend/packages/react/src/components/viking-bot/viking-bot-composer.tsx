import {
  forwardRef,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';

import { DEFAULT_LABELS } from './labels.js';
import type { VikingBotAttachment, VikingBotDraft } from './model.js';
import type { VikingBotComposerProps, VikingBotComposerRef } from './types.js';

/** 占位组件：仅按 props 契约渲染可用视图，未经设计评审，真实实现待重写。 */
export const VikingBotComposer = forwardRef<
  VikingBotComposerRef,
  VikingBotComposerProps
>(function VikingBotComposer(
  {
    value,
    className,
    attachments = [],
    disabled,
    disabledReason,
    isSending,
    variant = 'default',
    accept,
    maxAttachments = 10,
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
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [composing, setComposing] = useState(false);
  const readOnly = !onValueChange;
  const limitReached = attachments.length >= maxAttachments;

  useImperativeHandle(ref, () => ({
    setText: (text: string) => onValueChange?.(text),
    appendText: (text: string) => onValueChange?.(`${value}${text}`),
    addFiles: (files: readonly File[]) => onFilesAdded?.(files),
    isAttachmentLimitReached: () => limitReached,
    clear: () => onValueChange?.(''),
    focus: () => inputRef.current?.focus(),
  }));

  const submit = () => {
    if (!onSend || disabled || isSending) return;
    onSend({ text: value, attachments });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    if (composing || event.nativeEvent.isComposing) return;
    event.preventDefault();
    submit();
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
              <span className="ov-viking-bot__attachment-status">
                {attachment.status === 'error'
                  ? (attachment.errorMessage ?? labels.toolError)
                  : null}
              </span>
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
      <textarea
        ref={inputRef}
        className="ov-viking-bot__input"
        value={value}
        placeholder={labels.placeholder}
        aria-label={labels.placeholder}
        disabled={disabled}
        readOnly={readOnly}
        onChange={(event) => onValueChange?.(event.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
      />
      <div className="ov-viking-bot__composer-actions">
        {onFilesAdded ? (
          <>
            <input
              ref={fileRef}
              type="file"
              accept={accept}
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
              aria-label={labels.attach}
              disabled={disabled || limitReached}
              onClick={() => fileRef.current?.click()}
            >
              {labels.attach}
            </button>
          </>
        ) : null}
        {actions}
        {isSending ? (
          <button type="button" onClick={() => onCancel?.()}>
            {labels.stop}
          </button>
        ) : (
          <button
            type="button"
            disabled={disabled || !onSend}
            onClick={() => submit()}
          >
            {labels.send}
          </button>
        )}
      </div>
    </div>
  );
});

export type { VikingBotAttachment, VikingBotDraft };
