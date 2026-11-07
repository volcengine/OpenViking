import { FileViewerCore } from '@ark-design/ui/file-viewer/core';
import type {
  FileViewerCoreProps,
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import { useMemo } from 'react';

import { createFilePreviewAdapters } from './file-preview-handle-adapters.js';
import { toFileSource } from './file-preview-source.js';
import {
  LoadFailedState,
  LoadingState,
  UnsupportedState,
} from './file-preview-state.js';
import type {
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewViewMode,
} from './types.js';

export interface FilePreviewViewerProps {
  tab: FilePreviewFileTab;
  text: Required<FilePreviewLabels>;
  locale?: 'en-US' | 'zh-CN';
  viewMode: FilePreviewViewMode;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onDownload: () => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
}

/**
 * 文件页签的正文：把宿主的 `source` 交给底座按格式渲染。
 *
 * `toolbar` 恒为 `false`，外壳全部由组件渲染；兜底态通过 `slots` 换成组件
 * 自己的文案，底座只负责格式路由、渲染器按需加载与准入。
 *
 * 本模块会静态引入底座，只能由懒加载入口引用，见 `file-preview-body.tsx`。
 *
 * The body of a file tab: hands the host `source` to the base component for
 * format rendering. `toolbar` stays `false` because the component renders the
 * whole shell, and the fallback states are replaced through `slots` so the copy
 * comes from this component. The base keeps format routing, on-demand renderer
 * loading and admission. This module statically imports the base, so only the
 * lazy entry may reference it; see `file-preview-body.tsx`.
 */
export function FilePreviewViewer({
  tab,
  text,
  locale,
  viewMode,
  onViewModeChange,
  onDownload,
  onHandleChange,
  onViewModesChange,
}: FilePreviewViewerProps) {
  const source = useMemo(() => toFileSource(tab.source), [tab.source]);
  const adapters = useMemo(
    () => createFilePreviewAdapters({ onHandleChange, onViewModesChange }),
    [onHandleChange, onViewModesChange],
  );

  const slots = useMemo<FileViewerCoreProps['slots']>(() => {
    const loading = () => <LoadingState text={text} />;
    const error: NonNullable<FileViewerCoreProps['slots']>['error'] = ({
      retry,
    }) => <LoadFailedState text={text} onRetry={retry} />;
    const downloadFallback: NonNullable<
      FileViewerCoreProps['slots']
    >['downloadFallback'] = () => (
      <UnsupportedState text={text} onDownload={onDownload} />
    );
    const confirmFallback = tab.slots?.confirmFallback;
    return {
      loading: tab.slots?.loadingFallback ? undefined : loading,
      error: tab.slots?.errorFallback ? undefined : error,
      downloadFallback: tab.slots?.downloadFallback ?? downloadFallback,
      ...(confirmFallback ? { confirm: confirmFallback } : {}),
    };
  }, [onDownload, tab.slots, text]);

  if (!source) {
    return <UnsupportedState text={text} onDownload={onDownload} />;
  }

  return (
    <FileViewerCore
      key={tab.key}
      adapters={adapters}
      className="ov-file-preview__viewer"
      locale={locale}
      resources={tab.resources}
      slots={slots}
      source={source}
      toolbar={false}
      viewMode={viewMode}
      onViewModeChange={(mode) => {
        if (mode === 'preview' || mode === 'source') onViewModeChange(mode);
      }}
    />
  );
}
