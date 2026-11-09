import type { RichTextValue, SenderInstance } from '@ark-design/ui/sender';

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

/** Read the real editor caret in the same text coordinate space as the draft. */
export function composerCaret(
  sender: SenderInstance | null,
): number | undefined {
  const editor = sender?.getEditor();
  if (!editor?.state.selection.empty) return undefined;
  const { doc, selection } = editor.state;
  return doc.textBetween(0, selection.from, '\n', '\n').length;
}
