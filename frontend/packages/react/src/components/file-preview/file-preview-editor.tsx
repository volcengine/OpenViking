import { markFault } from '../../shared/monitoring/fault.js';
import { useCodeLoad } from '../../shared/monitoring/use-code-load.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import {
  ErrorNotification,
  errorMessage,
} from '../../shared/feedback/error-notification.js';
import type { Locale } from '../../shared/locale.js';
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
  saveFailure?: { reason: unknown };
  locale?: Locale;
  saving: boolean;
  onChange: (value: string) => void;
  onCancel: () => void;
}

const loadCodeEditor = async () => {
  const result = await import('./file-preview-code-editor.js');
  if (!result.FilePreviewCodeEditor)
    throw markFault(
      new Error('Required editor export is missing'),
      'configuration',
      'RESOURCE_EXPORT_INVALID',
    );
  return result;
};

/** Load CodeMirror only when editing; retain the draft if loading fails. */
export function FilePreviewEditor({
  draft,
  source,
  text,
  loading,
  failed,
  saveFailure,
  locale,
  saving,
  onChange,
  onCancel,
}: FilePreviewEditorProps) {
  const { load: loadEditor } = useCodeLoad(
    loadCodeEditor,
    'file_preview_editor_load',
  );
  const [Editor, setEditor] =
    useState<ComponentType<FilePreviewCodeEditorProps>>();
  const [editorFailure, setEditorFailure] = useState<{ reason: unknown }>();
  const [attempt, setAttempt] = useState(0);
  // Text inputs match :focus-visible even on pointer focus; track only this editor.
  const [pointerFocus, setPointerFocus] = useState(false);
  // useEffect 必要性：正文可编辑后，按读取状态和重试次数加载浏览器编辑器模块；
  // 异步 import 必须在客户端提交后执行，React 渲染无法直接取得模块结果。
  // 依赖变化或卸载时使旧结果失效，避免旧加载任务安装控件或更新错误状态。
  useEffect(() => {
    if (loading || failed) return;
    let active = true;
    setEditorFailure(undefined);
    void loadEditor()
      .then((module) => {
        if (active) setEditor(() => module.FilePreviewCodeEditor);
      })
      .catch((reason: unknown) => {
        if (active) setEditorFailure({ reason });
      });
    return () => {
      active = false;
    };
  }, [loading, failed, attempt, loadEditor]);

  const exposure = useExposure(
    'file_preview_source_show',
    source.path ?? source.fileName,
    undefined,
    !loading && !failed && Boolean(Editor || editorFailure),
  );
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
    <div
      ref={exposure}
      className="ov-file-preview__editor"
      data-pointer-focus={pointerFocus || undefined}
      onPointerDownCapture={() => setPointerFocus(true)}
      onKeyDownCapture={(event) => {
        if (event.key === 'Tab') setPointerFocus(false);
      }}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          setPointerFocus(false);
      }}
    >
      {Editor ? (
        <Editor
          draft={draft}
          source={source}
          saving={saving}
          text={text}
          onChange={onChange}
        />
      ) : editorFailure ? (
        <>
          <textarea
            className="ov-file-preview__textarea"
            value={draft}
            readOnly={saving}
            aria-label={text.editor}
            spellCheck={false}
            onChange={(event) => onChange(event.target.value)}
          />
          <ErrorNotification
            title={text.editorLoadFailed}
            identity={editorFailure}
            error={editorFailure.reason}
            locale={locale}
          >
            {errorMessage(editorFailure.reason)}
          </ErrorNotification>
          <Button
            size="small"
            title={text.editorLoadFailed}
            onClick={() => setAttempt((value) => value + 1)}
          >
            {text.retry}
          </Button>
        </>
      ) : (
        <p className="ov-file-preview__notice">{text.loading}</p>
      )}
      {saveFailure ? (
        <ErrorNotification
          title={text.saveFailed}
          identity={saveFailure}
          error={saveFailure.reason}
          locale={locale}
        >
          {errorMessage(saveFailure.reason)}
        </ErrorNotification>
      ) : null}
    </div>
  );
}
