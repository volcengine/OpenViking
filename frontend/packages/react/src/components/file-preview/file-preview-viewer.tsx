import { FileViewerCore } from '@ark-design/ui/file-viewer/core';
import type {
  FileViewerCoreProps,
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import { useMemo, useRef } from 'react';

import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

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
  FilePreviewSource,
  FilePreviewViewMode,
} from './types.js';

/**
 * `source` 的 memo 依赖。逐项列出而不是取摘要：字符串按值比较，长度与首尾
 * 片段相同的两份正文会被摘要判成同一份，底座就会一直显示旧内容；`File`/`Blob`
 * 按对象身份比较，同名同路径换一个实例也能被认出来。
 *
 * The memo dependency for `source`. Every field is listed rather than hashed:
 * strings compare by value, and a digest built from length plus edges would treat
 * two same-length bodies with matching ends as identical, leaving the base on the
 * old content; `File`/`Blob` compare by identity, so a different instance under
 * the same name and path is still detected.
 */
function sourceDeps(source: FilePreviewSource) {
  return [
    source.content,
    source.file,
    source.url,
    source.fileName,
    source.fileType,
    source.path,
  ] as const;
}

export interface FilePreviewViewerProps {
  tab: FilePreviewFileTab;
  text: Required<FilePreviewLabels>;
  locale?: 'en-US' | 'zh-CN';
  viewMode: FilePreviewViewMode;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onDownload: () => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  /** Markdown 正文里的链接被点击时调用；传入即由宿主接管导航。 */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
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
  onLinkClick,
}: FilePreviewViewerProps) {
  // 宿主的 `tabs` 数组每次渲染都会重建，`activeTab` 因此也是新对象，`tab.source`
  // 的引用每帧都变。`source` 是底座的加载触发点——给它一个新对象，底座会当成
  // 换了文件，把渲染器整个重挂一次。按内容取 memo 依赖，让它只在真正换文件时
  // 才变。`content` 可能很大，用长度与首尾片段做指纹，避免每次深比较整份正文。
  //
  // The host rebuilds its `tabs` array on every render, so `activeTab` is a new
  // object and `tab.source`'s identity changes every frame. `source` is what the
  // base loads — a new object reads as "a different file" and makes it remount the
  // whole renderer. Derive the memo dependency from the content so it only changes
  // when the file really does. `content` can be large, so a length-plus-edges
  // fingerprint stands in for a deep comparison of the whole body.
  const source = useMemo(
    () => toFileSource(tab.source),
    // 逐项列出 source 的字段而不是传整个对象：宿主每次渲染都会重建 tabs，
    // `tab.source` 的引用每帧都变，直接传它会让底座当成换了文件而重挂渲染器。
    //
    // List the fields instead of passing the object: the host rebuilds its tabs on
    // every render, so `tab.source`'s identity changes each frame and passing it
    // would make the base treat it as a new file and remount the renderer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    sourceDeps(tab.source),
  );
  const adapters = useMemo(
    () =>
      createFilePreviewAdapters({
        onHandleChange,
        onViewModesChange,
        onLinkClick,
      }),
    [onHandleChange, onViewModesChange, onLinkClick],
  );

  // 底座把 `slots` 里的函数当依赖，换新函数会重建渲染器。而这三个来源在宿主
  // 那边每次渲染都是新值：`tabText` 是新建对象、`onDownload` 是内联箭头函数、
  // `tab.slots` 写在 tabs 数组里同样是字面量。若直接进依赖数组，`slots` 就每次
  // 都变，表现为点击任意按钮后整个正文「闪一下」——渲染器被卸载重建。
  //
  // 全部用 ref 持有最新值，让 `slots` 的依赖数组为空：它只在本次挂载时创建
  // 一次，之后正文在复制、切换视图等操作下保持挂载。切页签时 `key` 变化会让
  // 整个组件重建，ref 随之更新，因此不会读到上一个页签的值。
  //
  // The base treats the functions inside `slots` as dependencies and remounts the
  // renderer when they change. All three sources are fresh on every host render:
  // `tabText` is a new object, `onDownload` an inline arrow, and `tab.slots` a
  // literal inside the tabs array. Putting them in the dependency array made
  // `slots` change on every render — which showed up as the whole body "flashing"
  // after any click, the renderer being torn down and rebuilt.
  //
  // A ref holds the latest values so `slots` has an empty dependency array: it is
  // built once per mount and the body stays mounted across copy, view switch and
  // friends. Switching tabs changes `key`, which remounts everything and refreshes
  // the ref, so it never reads the previous tab's values.
  const latest = useRef({ text, onDownload, tabSlots: tab.slots });
  latest.current = { text, onDownload, tabSlots: tab.slots };

  const slots = useMemo<FileViewerCoreProps['slots']>(() => {
    const loading = () => <LoadingState text={latest.current.text} />;
    const error: NonNullable<FileViewerCoreProps['slots']>['error'] = ({
      retry,
    }) => <LoadFailedState text={latest.current.text} onRetry={retry} />;
    const downloadFallback: NonNullable<
      FileViewerCoreProps['slots']
    >['downloadFallback'] = () => (
      <UnsupportedState
        text={latest.current.text}
        onDownload={latest.current.onDownload}
      />
    );
    const own = latest.current.tabSlots;
    // 宿主给了兜底就渲染它，没给才用内置的。写成 `own?.x ? undefined : 内置`
    // 是反的：那样宿主提供之后底座会退回自己的默认渲染，宿主的节点永远不会
    // 出现。
    //
    // Use the host's fallback when it supplies one, the built-in otherwise.
    // Writing `own?.x ? undefined : builtIn` inverts it: the base would fall back
    // to its own rendering and the host's node would never show.
    const loadingSlot = own?.loadingFallback
      ? () => <>{latest.current.tabSlots?.loadingFallback}</>
      : loading;
    const errorSlot: NonNullable<FileViewerCoreProps['slots']>['error'] =
      own?.errorFallback
        ? () => <>{latest.current.tabSlots?.errorFallback}</>
        : error;
    const confirmFallback = own?.confirmFallback;
    // 底座按 `slots.downloadFallback` → `unsupportedFallback` → 内置兜底的顺序
    // 取格式不支持时的内容。宿主给了 `unsupportedFallback` 就整体接管这个状态，
    // 组件必须让出自己的 `downloadFallback`，否则永远命中它、宿主的节点轮不到。
    //
    // The base resolves the unsupported state as `slots.downloadFallback` →
    // `unsupportedFallback` → its own message. A host `unsupportedFallback` takes
    // over that state wholesale, so this component has to step aside with its own
    // `downloadFallback`; otherwise ours always wins and the host's never shows.
    const hostUnsupported = latest.current.tabSlots?.unsupportedFallback;
    return {
      loading: loadingSlot,
      error: errorSlot,
      ...(hostUnsupported
        ? {}
        : { downloadFallback: own?.downloadFallback ?? downloadFallback }),
      ...(confirmFallback ? { confirm: confirmFallback } : {}),
    };
  }, []);

  if (!source) {
    // 连可预览的来源都没有，底座无从判断格式，只能由组件兜底。
    return (
      tab.slots?.unsupportedFallback ?? (
        <UnsupportedState text={text} onDownload={onDownload} />
      )
    );
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
      // 格式是否可预览由底座判定，宿主的兜底内容因此交给它，而不是在组件
      // 这边提前判断。底座的选择顺序是 `slots.downloadFallback` →
      // `unsupportedFallback` → 内置兜底，所以传这个 prop 前必须先收掉
      // 自己的 `downloadFallback`，否则永远命中前者。
      //
      // Whether a format can be previewed is the base's call, so the host's
      // fallback goes to it rather than being decided here. The base tries
      // `slots.downloadFallback` first, then `unsupportedFallback`, then its own
      // message — so our own `downloadFallback` has to step aside for this prop to
      // ever be reached.
      unsupportedFallback={tab.slots?.unsupportedFallback}
      viewMode={viewMode}
      onViewModeChange={(mode) => {
        if (mode === 'preview' || mode === 'source') onViewModeChange(mode);
      }}
    />
  );
}
