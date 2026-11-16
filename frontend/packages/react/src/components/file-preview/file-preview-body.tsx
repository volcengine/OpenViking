import { MonitoringFlow } from '../../shared/monitoring/flow-context.js';
import { useContentMonitoring } from './use-content-monitoring.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import { useState } from 'react';
import { useRenderErrors } from './file-preview-labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import type { Locale } from '../../shared/locale.js';
import type {
  FileViewerState,
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { FilePreviewMedia, mediaKind } from './file-preview-media.js';
import { FilePreviewImageFrame } from './file-preview-image-frame.js';
import { FilePreviewEditor } from './file-preview-editor.js';
import { isEmptyFile } from './file-preview-source.js';
import { EmptyFileState, LoadingState } from './file-preview-state.js';
import { LazyFilePreviewViewer } from './file-preview-lazy-viewer.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewViewMode,
} from './types.js';

export interface FilePreviewBodyProps {
  monitoringTargetKey?: string;
  fileTab: FilePreviewFileTab;
  text: Required<FilePreviewLabels>;
  locale?: Locale;
  viewMode: FilePreviewViewMode;
  editing: ReturnType<typeof useFilePreviewEditing>;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  /** Markdown 正文里的链接被点击时调用。 */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
  /** 读取失败后退出编辑态。Leaves editing after a failed load. */
  onCancelEditing: () => void;
  /** 底座加载状态变化时调用；过渡层用它判断新内容何时就绪。 */
  /** Called when the base's load state changes; the transition uses it as the ready signal. */
  onStateChange?: (
    state: FileViewerState,
    error?: unknown,
    reason?: 'retry',
  ) => void;
  /**
   * 是否向外上报句柄与视图模式。过渡期间后台那一层传 `false`，避免它覆盖屏幕上
   * 那一层的句柄。
   *
   * Whether handles and view modes are reported outward. The background layer passes
   * `false` during a transition so it cannot overwrite the visible layer's handle.
   */
  reports?: boolean;
  /** 这一层自己的加载态开始与结束时调用。 Called when this layer's own loading starts and ends. */
  onLoadingChange?: (loading: boolean) => void;
}

/**
 * 文件页签的正文：按编辑态、空文件、底座依次短路。内容页签不走这里——它的
 * 正文整块由宿主渲染。
 *
 * The body of a file tab: it short-circuits through editing, empty file and then
 * the base component. A content tab never reaches this — its body is the host's.
 */
function FilePreviewBodyContent({
  fileTab,
  text,
  locale,
  viewMode,
  editing,
  onViewModeChange,
  onHandleChange,
  onViewModesChange,
  onLinkClick,
  onCancelEditing,
  onStateChange,
  reports = true,
  onLoadingChange,
}: FilePreviewBodyProps) {
  const [ready, setReady] = useState(false);
  const exposure = useExposure(
    viewMode === 'source'
      ? 'file_preview_source_show'
      : 'file_preview_content_show',
    fileTab.key + ':' + viewMode,
    undefined,
    ready && reports,
  );
  // `editFailed` 也要走编辑器分支：读取失败时 `editing` 与 `loadingEdit` 都是
  // false，只检查这两项会直接落回普通预览，用户看不到读取失败的提示。
  //
  // `editFailed` belongs here too: on a failed load both `editing` and
  // `loadingEdit` are false, so checking only those two falls back to the plain
  // preview and the failure message never shows.
  if (editing.editing || editing.loadingEdit || editing.editFailed) {
    return (
      <FilePreviewEditor
        draft={editing.draft}
        source={fileTab.source}
        text={text}
        loading={editing.loadingEdit}
        failed={editing.editFailed}
        saveFailure={editing.saveFailure}
        locale={locale}
        saving={editing.saving}
        onChange={editing.updateDraft}
        onCancel={onCancelEditing}
      />
    );
  }

  // 加载中优先于空文件：`source` 还没填上，不能当成「文件为空」。
  // Loading outranks empty: `source` is not filled in yet, which is not an empty file.
  if (fileTab.loading)
    return fileTab.slots?.loadingFallback ?? <LoadingState text={text} />;

  if (isEmptyFile(fileTab.source)) return <EmptyFileState text={text} />;

  const media = mediaKind(fileTab.source);
  if (media)
    return (
      <FilePreviewMedia
        source={fileTab.source}
        kind={media}
        text={text}
        slots={fileTab.slots}
        reports={reports}
        onStateChange={onStateChange}
      />
    );

  return (
    <FilePreviewImageFrame
      elementRef={exposure}
      key={JSON.stringify([
        fileTab.source.path,
        fileTab.source.fileName,
        fileTab.source.url,
      ])}
    >
      <LazyFilePreviewViewer
        tab={fileTab}
        text={text}
        locale={locale}
        viewMode={viewMode}
        onViewModeChange={onViewModeChange}
        onHandleChange={onHandleChange}
        onViewModesChange={onViewModesChange}
        onLinkClick={onLinkClick}
        onStateChange={(state) => {
          setReady(state === 'ready');
          onStateChange?.(state);
        }}
        reports={reports}
        onLoadingChange={onLoadingChange}
      />
    </FilePreviewImageFrame>
  );
}

export function FilePreviewBody(props: FilePreviewBodyProps) {
  const { source } = props.fileTab;
  const monitoring = useContentMonitoring(props);
  return (
    <MonitoringFlow operation={monitoring.operation}>
      <ErrorBoundary
        monitoringRegion={props.viewMode === 'source' ? 'source' : 'content'}
        message={
          useRenderErrors(props.locale)[
            props.editing.editing ||
            props.editing.loadingEdit ||
            props.editing.editFailed
              ? 'editor'
              : 'preview'
          ]
        }
        locale={props.locale}
        fallback={props.fileTab.slots?.errorFallback}
        onReset={monitoring.reset}
        onError={(error) => {
          monitoring.caught(error);
          // Promote failed background bodies so their fallback and retry are reachable.
          props.onLoadingChange?.(false);
          props.onStateChange?.('error');
        }}
        resetKeys={[
          props.fileTab.key,
          props.fileTab.loading,
          source.path,
          source.fileName,
          source.fileType,
          source.content,
          source.url,
          source.file,
          props.viewMode,
        ]}
      >
        <FilePreviewBodyContent
          {...props}
          onLoadingChange={(loading) => {
            monitoring.loading(loading);
            props.onLoadingChange?.(loading);
          }}
          onStateChange={(state, error, reason) => {
            monitoring.observe(state, error, reason);
            props.onStateChange?.(state, error, reason);
          }}
        />
      </ErrorBoundary>
    </MonitoringFlow>
  );
}
