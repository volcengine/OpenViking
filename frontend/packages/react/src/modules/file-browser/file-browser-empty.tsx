/**
 * 未选中资源时的空状态：插画在上、提示文案在下，整体居中。
 *
 * 插画来自设计位图，提示保留为可翻译的文本。
 *
 * The empty state shown before anything is selected: artwork above, hint below,
 * centred as a whole.
 *
 * The artwork comes from the design bitmap; the hint stays localizable text.
 */
export function FileBrowserEmpty({ label }: { label: string }) {
  return (
    <div className="ov-file-browser__empty">
      <div className="ov-file-browser__empty-art" aria-hidden="true" />
      <p className="ov-file-browser__empty-label">{label}</p>
    </div>
  );
}
