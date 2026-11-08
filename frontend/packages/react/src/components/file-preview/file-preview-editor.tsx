import type { FilePreviewLabels } from './types.js';

export interface FilePreviewEditorProps {
  draft: string;
  text: Required<FilePreviewLabels>;
  loading: boolean;
  failed: boolean;
  saveFailed: boolean;
  saving: boolean;
  onChange: (value: string) => void;
}

/**
 * 编辑态正文：纯文本编辑区，不经过底座。
 *
 * 编辑区独立于 `FileViewerCore`，因此进入编辑不会重建渲染器，退出编辑也
 * 不会重新解析内容。
 *
 * The editing body: a plain-text area that bypasses the base component. Keeping
 * it separate means entering or leaving editing never remounts the renderer nor
 * reparses the content.
 */
export function FilePreviewEditor({
  draft,
  text,
  loading,
  failed,
  saveFailed,
  saving,
  onChange,
}: FilePreviewEditorProps) {
  if (loading) {
    return (
      <div className="ov-file-preview__state" role="status">
        <p className="ov-file-preview__state-title">{text.loading}</p>
      </div>
    );
  }

  if (failed) {
    return (
      <div className="ov-file-preview__state" role="alert">
        <p className="ov-file-preview__state-title">{text.loadFailed}</p>
      </div>
    );
  }

  return (
    <div className="ov-file-preview__editor">
      <textarea
        className="ov-file-preview__textarea"
        value={draft}
        readOnly={saving}
        aria-label={text.editor}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
      {saveFailed ? (
        <p className="ov-file-preview__notice" role="alert">
          {text.saveFailed}
        </p>
      ) : null}
    </div>
  );
}
