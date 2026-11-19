import type { RichTextValue, SenderInstance } from '@ark-design/ui/sender';
import { useEffect, type RefObject } from 'react';

/** Plain text excludes attachment/tag values, matching the controlled draft. */
export const toPlainText = (content: RichTextValue[]): string =>
  content
    .map((item) =>
      item.type === 'hardBreak'
        ? '\n'
        : item.type === 'text'
          ? (item.text ?? '')
          : '',
    )
    .join('');

export function toRichText(text: string): RichTextValue[] {
  const content: RichTextValue[] = [];
  text.split('\n').forEach((line, index) => {
    if (index) content.push({ type: 'hardBreak' });
    if (line) content.push({ type: 'text', text: line });
  });
  return content;
}

/** Sender.setValue dispatches synchronous content events; these are not user edits. */
export function writeComposerText(
  sender: SenderInstance | null,
  text: string,
  writing: { current: boolean },
) {
  const previous = writing.current;
  writing.current = true;
  try {
    sender?.setValue(toRichText(text));
  } finally {
    writing.current = previous;
  }
}

// useEffect 必要性：把受控 value 的后续变化同步到 Ark 编辑器。编辑器是外部
// 非 React 控件，只有它的 setValue 能改写内部文档，渲染计算无法做到。仅当
// 外部文本与编辑器当前纯文本不一致时才写入，避免覆盖用户正在输入的光标。
// useQuickInput 已按受控值更新归属；程序写入的同步回调不能再用光标认领文本。
// 组件卸载或 value 再次变化时由下一次同步覆盖，无需额外清理。
export function useSyncComposerValue(
  sender: RefObject<SenderInstance | null>,
  value: string,
  writing: { current: boolean },
) {
  useEffect(() => {
    const current = toPlainText(sender.current?.getValue() ?? []);
    if (current === value) return;
    writeComposerText(sender.current, value, writing);
  }, [sender, value, writing]);
}

/** Read the real editor caret in the same text coordinate space as the draft. */
export function composerCaret(
  sender: SenderInstance | null,
): number | undefined {
  const editor = sender?.getEditor();
  if (!editor?.state.selection.empty) return undefined;
  const { doc, selection } = editor.state;
  return doc.textBetween(0, selection.from, '\n', '\n').length;
}
