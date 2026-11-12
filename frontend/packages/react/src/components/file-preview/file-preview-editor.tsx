import { useEffect, useState, type ComponentType } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import type { FilePreviewLabels, FilePreviewSource } from './types.js';
import type { FilePreviewCodeEditorProps } from './file-preview-code-editor.js';

export interface FilePreviewEditorProps {
  draft: string;
  source: FilePreviewSource;
  text: Required<FilePreviewLabels>;
  loading: boolean;
  failed: boolean;
  saveFailed: boolean;
  saving: boolean;
  onChange: (value: string) => void;
  onCancel: () => void;
}

/** Load CodeMirror only when editing; retain the draft if loading fails. */
export function FilePreviewEditor({
  draft,
  source,
  text,
  loading,
  failed,
  saveFailed,
  saving,
  onChange,
  onCancel,
}: FilePreviewEditorProps) {
  const [Editor, setEditor] =
    useState<ComponentType<FilePreviewCodeEditorProps>>();
  const [editorFailed, setEditorFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (loading || failed) return;
    let active = true;
    setEditorFailed(false);
    void import('./file-preview-code-editor.js')
      .then((module) => {
        if (active) setEditor(() => module.FilePreviewCodeEditor);
      })
      .catch(() => {
        if (active) setEditorFailed(true);
      });
    return () => {
      active = false;
    };
  }, [loading, failed, attempt]);

  if (loading) {
    return (
      <div className="ov-file-preview__state" role="status">
        <p className="ov-file-preview__state-title">{text.loading}</p>
      </div>
    );
  }

  if (failed) {
    // 读取失败后必须留一条退路：否则用户停在错误提示上，既没有草稿可编辑，
    // 也没有办法回到普通预览。
    //
    // A failed load needs a way out: otherwise the user is stranded on the error
    // with no draft to edit and no way back to the plain preview.
    return (
      <div className="ov-file-preview__state" role="alert">
        <p className="ov-file-preview__state-title">{text.loadFailed}</p>
        <Button
          size="small"
          type="default"
          htmlType="button"
          className="ov-file-preview__button"
          onClick={onCancel}
        >
          {text.leaveEditing}
        </Button>
      </div>
    );
  }

  return (
    <div className="ov-file-preview__editor">
      {Editor ? (
        <Editor
          draft={draft}
          source={source}
          saving={saving}
          text={text}
          onChange={onChange}
        />
      ) : editorFailed ? (
        <>
          <textarea
            className="ov-file-preview__textarea"
            value={draft}
            readOnly={saving}
            aria-label={text.editor}
            spellCheck={false}
            onChange={(event) => onChange(event.target.value)}
          />
          <div className="ov-file-preview__notice" role="alert">
            {text.editorLoadFailed}{' '}
            <Button
              size="small"
              onClick={() => setAttempt((value) => value + 1)}
            >
              {text.retry}
            </Button>
          </div>
        </>
      ) : (
        <p className="ov-file-preview__notice">{text.loading}</p>
      )}
      {saveFailed ? (
        <p className="ov-file-preview__notice" role="alert">
          {text.saveFailed}
        </p>
      ) : null}
    </div>
  );
}
