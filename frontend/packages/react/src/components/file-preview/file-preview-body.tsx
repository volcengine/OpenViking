import styles from './file-preview.module.css';
import type {
  FilePreviewContentTab,
  FilePreviewFileTab,
  FilePreviewLabels,
} from './types.js';

export interface FilePreviewBodyProps {
  fileTab?: FilePreviewFileTab;
  contentTab?: FilePreviewContentTab;
  text: Required<FilePreviewLabels>;
  editing: boolean;
  loadingEdit: boolean;
  editFailed: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
}

/** 内容区：内容页签原样渲染，文件页签按编辑态、取正文状态与文本降级依次分支。 */
export function FilePreviewBody({
  fileTab,
  contentTab,
  text,
  editing,
  loadingEdit,
  editFailed,
  draft,
  onDraftChange,
}: FilePreviewBodyProps) {
  if (contentTab) return contentTab.content;
  if (!fileTab) return null;

  const { source } = fileTab;

  if (loadingEdit) {
    return (
      <div className={styles.state}>
        <span className={styles.muted}>{text.loading}</span>
      </div>
    );
  }

  if (editFailed) {
    return (
      <div className={styles.state} data-tone="error">
        <span className={styles.muted}>{text.loadFailed}</span>
      </div>
    );
  }

  if (editing && fileTab.editing) {
    return (
      <div className={styles.editor}>
        <textarea
          className={styles.textarea}
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
        />
      </div>
    );
  }

  if (typeof source.content !== 'string') {
    return (
      <div className={styles.state}>
        {fileTab.slots?.unsupportedFallback ?? (
          <span className={styles.muted}>
            {source.file || source.url
              ? text.binaryUnsupported
              : text.unsupported}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className={styles.textBody}>
      <pre className={styles.pre}>{source.content}</pre>
    </div>
  );
}
