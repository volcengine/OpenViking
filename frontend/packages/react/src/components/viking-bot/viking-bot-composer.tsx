import { Sender } from '@ark-design/ui/sender';
import type { RichTextValue, SenderInstance } from '@ark-design/ui/sender';
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react';

import { DEFAULT_LABELS } from './labels.js';
import type { VikingBotDraft } from './model.js';
import type { VikingBotComposerProps, VikingBotComposerRef } from './types.js';

const HARD_BREAK = 'hardBreak';
const TEXT = 'text';

/** 从富文本里取纯文本，忽略附件与标签。 */
const toPlainText = (content: RichTextValue[]): string =>
  content
    .map((item) => {
      if (item.type === HARD_BREAK) return '\n';
      return item.type === TEXT ? (item.text ?? '') : '';
    })
    .join('')
    .trim();

const toRichText = (text: string): RichTextValue[] =>
  text ? [{ type: TEXT, text }] : [];

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
  const senderRef = useRef<SenderInstance>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const readOnly = !onValueChange;
  const remaining = Math.max(0, maxAttachments - attachments.length);
  const limitReached = remaining === 0;

  const setText = useCallback(
    (text: string) => {
      senderRef.current?.setValue(toRichText(text));
      onValueChange?.(text);
    },
    [onValueChange],
  );

  // useEffect 必要性：把受控 value 的后续变化同步到 Ark 编辑器。编辑器是外部
  // 非 React 控件，只有它的 setValue 能改写内部文档，渲染计算无法做到。仅当
  // 外部文本与编辑器当前纯文本不一致时才写入，避免覆盖用户正在输入的光标。
  // 组件卸载或 value 再次变化时由下一次同步覆盖，无需额外清理。
  useEffect(() => {
    const current = toPlainText(senderRef.current?.getValue() ?? []);
    if (current === value) return;
    senderRef.current?.setValue(toRichText(value));
  }, [value]);

  /** 按剩余额度截断并上报文件。 */
  const reportFiles = useCallback(
    (files: readonly File[]) => {
      if (!onFilesAdded || files.length === 0 || limitReached) return;
      const accepted = files.slice(0, remaining);
      if (accepted.length) onFilesAdded(accepted);
    },
    [limitReached, onFilesAdded, remaining],
  );

  useImperativeHandle(ref, () => ({
    setText,
    appendText: (text: string) => setText(`${value}${text}`),
    addFiles: (files: readonly File[]) => reportFiles(files),
    isAttachmentLimitReached: () => limitReached,
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

  // 拦截拖放的默认行为与是否接受文件是两件事：满额或禁用时仍要阻止浏览器
  // 直接打开被拖入的文件（那会离开当前会话并丢掉未发送草稿），只是不添加文件。
  const interceptDrop = Boolean(onFilesAdded);
  const acceptDrop = interceptDrop && !disabled && !limitReached;

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
        if (!interceptDrop) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = acceptDrop ? 'copy' : 'none';
      }}
      onDrop={(event) => {
        if (!interceptDrop) return;
        // 无论能否接受，都先取消默认行为，避免浏览器打开该文件。
        event.preventDefault();
        if (!acceptDrop) return;
        reportFiles([...(event.dataTransfer.files ?? [])]);
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
                  accept={accept}
                  multiple
                  hidden
                  onChange={(event) => {
                    reportFiles([...(event.target.files ?? [])]);
                    event.target.value = '';
                  }}
                />
                <button
                  type="button"
                  disabled={disabled || limitReached}
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
