import type { ReactNode } from 'react';

import type { FilePreviewLabels } from './types.js';

/** 整块正文状态：居中、可滚动，用于加载、空、错误与不支持。 */
function StatePanel({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="ov-file-preview__state" role="status">
      {icon}
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

/**
 * 加载态。只用到 `loading` 一条文案，因此参数收窄到这一项：宿主在正文还没
 * 取回来时手上没有完整的一套文案，也不该为了显示加载中而编一份。
 *
 * A loading state. Only `loading` is read, so the parameter narrows to that one
 * field: a host that has not received the body yet has no full copy set, and should
 * not have to invent one just to show that something is loading.
 */
export function LoadingState({
  text,
}: {
  text: Pick<FilePreviewLabels, 'loading'>;
}) {
  return (
    <StatePanel
      title={text.loading ?? 'Loading…'}
      // 转圈放在标题上方：只写「加载中」看不出还在不在动，等待久一点就像卡住了。
      // The ring sits above the title: the words alone do not show that anything is
      // still happening, and a longer wait then reads as a hang.
      icon={<span className="ov-file-preview__spinner" />}
    />
  );
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
