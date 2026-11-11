import { useLocale } from '../../shared/locale-context.js';
import { Sender } from '@ark-design/ui/sender';
import type { SenderInstance } from '@ark-design/ui/sender';
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react';

import { resolveVikingBotLabels } from './labels.js';
import { VikingBotSubmit } from './composer-controls.js';
import { ComposerAttachments } from './composer-attachments.js';
import { composerMention } from './composer-references.js';
import { ReferenceTags } from './composer-reference-tags.js';
import { ComposerFooter } from './composer-footer.js';
import { QuickInputs } from './quick-inputs.js';
import { useQuickInput } from './use-quick-input.js';
import {
  composerCaret,
  toPlainText,
  toRichText,
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
  const readOnly = !onValueChange;
  const locked = Boolean(disabled || readOnly || isSending);
  const remaining = Math.max(0, maxAttachments - attachments.length);
  const limitReached = remaining === 0;

  const setText = useCallback(
    (text: string) => {
      writeComposerText(senderRef.current, text, writing);
      onValueChange?.(text);
    },
    [onValueChange],
  );
  const shortcuts = useQuickInput(value, setText, onQuickInputSelect);

  // useEffect 必要性：把受控 value 的后续变化同步到 Ark 编辑器。编辑器是外部
  // 非 React 控件，只有它的 setValue 能改写内部文档，渲染计算无法做到。仅当
  // 外部文本与编辑器当前纯文本不一致时才写入，避免覆盖用户正在输入的光标。
  // useQuickInput 已按受控值更新归属；程序写入的同步回调不能再用光标认领文本。
  // 组件卸载或 value 再次变化时由下一次同步覆盖，无需额外清理。
  useEffect(() => {
    const current = toPlainText(senderRef.current?.getValue() ?? []);
    if (current === value) return;
    writeComposerText(senderRef.current, value, writing);
  }, [value]);

  /** 按剩余额度截断并上报文件。 */
  const reportFiles = useCallback(
    (files: readonly File[]) => {
      if (files.length && remaining) onFilesAdded?.(files.slice(0, remaining));
    },
    [onFilesAdded, remaining],
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
    ).trim();
    onSend({
      text,
      attachments,
      ...(references ? { references: references.selected } : {}),
    });
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
      <QuickInputs
        items={quickInputs}
        disabled={disabled || readOnly || isSending}
        onSelect={(item) => {
          shortcuts.select(item);
          senderRef.current?.getEditor()?.commands.focus('end');
        }}
      />
      <Sender
        ref={senderRef}
        className="ov-viking-bot__sender"
        defaultContent={toRichText(value)}
        sending={isSending}
        sendDisabled={disabled || readOnly || (!isSending && !onSend)}
        onContentChange={(content) => {
          if (readOnly || writing.current) return;
          const next = toPlainText(content);
          shortcuts.tracker.edit(next, composerCaret(senderRef.current));
          onValueChange?.(next);
        }}
        onSend={submit}
        onStop={() => onCancel?.()}
        renderHeader={() => (
          <>
            <ReferenceTags
              config={references}
              labels={labels}
              onOpenUri={onOpenUri}
              disabled={disabled || isSending}
            />
            <ComposerAttachments
              items={attachments}
              onRemove={onAttachmentRemove}
              labels={labels}
              disabled={disabled || isSending}
            />
          </>
        )}
        renderSubmit={(props) =>
          isSending && !onCancel ? null : (
            <VikingBotSubmit
              {...props}
              label={isSending ? labels.stop : labels.send}
            />
          )
        }
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
            filesDisabled={Boolean(disabled || limitReached || isSending)}
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
