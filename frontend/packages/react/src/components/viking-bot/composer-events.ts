import type { SenderInstance } from '@ark-design/ui/sender';
import type { HTMLAttributes, RefObject } from 'react';

/** 输入面板里自带交互的元素；点在这些元素上时保留它们自己的行为。 */
const INTERACTIVE =
  'button, a, input, textarea, select, label, [contenteditable="true"], [role="button"], [role="textbox"]';

/**
 * Preserve IME confirmation, prevent file drops from navigating away, and focus the
 * editor when the blank area of the Sender panel is pressed.
 */
export function composerEvents(
  interceptDrop: boolean,
  acceptDrop: boolean,
  reportFiles: (files: readonly File[]) => void,
  sender: RefObject<SenderInstance | null>,
): Pick<
  HTMLAttributes<HTMLDivElement>,
  'onKeyDownCapture' | 'onDragOver' | 'onDrop' | 'onMouseDown'
> {
  return {
    // 点在 Sender 面板空白处时把光标放到编辑器末尾。阻止默认行为，避免编辑器先失焦再聚焦。
    // Pressing the blank area of the Sender panel puts the caret at the end of the
    // editor; the default is prevented so the editor does not blur and refocus.
    onMouseDown: (event) => {
      const target = event.target;
      if (
        event.button !== 0 ||
        !(target instanceof Element) ||
        !target.closest('.ov-viking-bot__sender') ||
        target.closest(INTERACTIVE)
      )
        return;
      const editor = sender.current?.getEditor();
      if (!editor?.isEditable) return;
      event.preventDefault();
      editor.commands.focus('end');
    },
    onKeyDownCapture: (event) => {
      if (
        event.key === 'Enter' &&
        (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)
      )
        event.stopPropagation();
    },
    onDragOver: (event) => {
      if (!interceptDrop) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = acceptDrop ? 'copy' : 'none';
    },
    onDrop: (event) => {
      if (!interceptDrop) return;
      event.preventDefault();
      if (acceptDrop) reportFiles([...(event.dataTransfer.files ?? [])]);
    },
  };
}
