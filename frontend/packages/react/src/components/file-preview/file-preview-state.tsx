import type { ReactNode } from 'react';

import type { FilePreviewLabels } from './types.js';

/** 整块正文状态：居中、可滚动，用于加载、空、错误与不支持。 */
function StatePanel({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="ov-file-preview__state" role="status">
      <p className="ov-file-preview__state-title">{title}</p>
      {description ? (
        <p className="ov-file-preview__state-description">{description}</p>
      ) : null}
      {action}
    </div>
  );
}

/** 空文件态：严格长度为 0。The empty-file state, for a strictly zero-length body. */
export function EmptyFileState({
  text,
}: {
  text: Required<FilePreviewLabels>;
}) {
  return <StatePanel title={text.fileEmpty} />;
}

/** 无可见内容态：正文存在但没有可渲染的文本。 */
export function NoContentState({
  text,
}: {
  text: Required<FilePreviewLabels>;
}) {
  return <StatePanel title={text.noContent} />;
}

/** 加载态。A loading state. */
export function LoadingState({ text }: { text: Required<FilePreviewLabels> }) {
  return <StatePanel title={text.loading} />;
}

/** 读取失败态，带重试入口。A read-failure state with a retry entry. */
export function LoadFailedState({
  text,
  description,
  onRetry,
}: {
  text: Required<FilePreviewLabels>;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <StatePanel
      title={text.loadFailed}
      description={description}
      action={
        onRetry ? (
          <button
            type="button"
            className="ov-file-preview__button"
            onClick={onRetry}
          >
            {text.retry}
          </button>
        ) : undefined
      }
    />
  );
}

/** 格式不支持态，带下载入口。An unsupported-format state with a download entry. */
export function UnsupportedState({
  text,
  onDownload,
}: {
  text: Required<FilePreviewLabels>;
  onDownload?: () => void;
}) {
  return (
    <StatePanel
      title={text.unsupported}
      action={
        onDownload ? (
          <button
            type="button"
            className="ov-file-preview__button"
            onClick={onDownload}
          >
            {text.download}
          </button>
        ) : undefined
      }
    />
  );
}

/** 正文被截断时的提示，固定在正文下方。 */
export function TruncatedNotice({
  text,
}: {
  text: Required<FilePreviewLabels>;
}) {
  return <p className="ov-file-preview__notice">{text.truncated}</p>;
}
