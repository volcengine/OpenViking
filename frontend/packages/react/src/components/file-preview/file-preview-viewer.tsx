import type { Locale } from '../../shared/locale.js';
import { FileViewerCore } from '@ark-design/ui/file-viewer/core';
import type {
  FileViewerCoreProps,
  FileViewerState,
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import { useCallback, useEffect, useMemo, useRef } from 'react';

import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { createFilePreviewAdapters } from './file-preview-handle-adapters.js';
import { FilePreviewLoadingReporter } from './file-preview-loading-reporter.js';
import { imagePreviewTarget } from './file-preview-markdown-images.js';
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
  locale?: Locale;
  viewMode: FilePreviewViewMode;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  /** Markdown 正文里的链接被点击时调用；传入即由宿主接管导航。 */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
  /** 底座加载状态变化时调用；过渡层用它判断新内容何时就绪。 */
  /** Called when the base's load state changes; the transition uses it as the ready signal. */
  onStateChange?: (state: FileViewerState) => void;
  /**
   * 是否向外上报句柄与视图模式。过渡期间后台那一层传 `false`。
   * Whether handles and view modes are reported outward; `false` for a background layer.
   */
  reports?: boolean;
  /** 这一层自己的加载态开始与结束时调用。 Called when this layer's own loading starts and ends. */
  onLoadingChange?: (loading: boolean) => void;
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
  onHandleChange,
  onViewModesChange,
  onLinkClick,
  onStateChange,
  reports = true,
  onLoadingChange,
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
  const imageInput = useRef(tab.source);
  imageInput.current = tab.source;
  const imageResolver = tab.resolveImage;
  const resolveImage = useCallback(
    (src: string) => imageResolver!(src, imageInput.current),
    [imageResolver],
  );
  const resources = useMemo(
    () =>
      tab.resources?.flatMap((resource) => {
        const key = imagePreviewTarget(resource.key);
        return key === resource.key
          ? [resource]
          : [resource, { ...resource, key }];
      }),
    [tab.resources],
  );
  // 后台层不向外上报：两个实例同时在树上，它的句柄与视图模式会覆盖屏幕上那个
  // 的。用 ref 取最新值，`adapters` 的依赖因此与 `reports` 无关——它一变就重建
  // 适配器，等于把正在后台加载的渲染器又重挂一次。
  //
  // A background layer reports nothing: both instances are on the tree and its
  // handle and view modes would overwrite the visible one's. A ref carries the latest
  // flag so `reports` stays out of the adapter dependencies — including it would
  // rebuild the adapters and remount the very renderer that is loading.
  const reportsRef = useRef(reports);
  reportsRef.current = reports;
  // 后台层被压下的那一次上报，换到前台时补发：底座只在渲染器挂载时交出句柄，
  // 切换只是翻转可见性、不会重新挂载，不补发的话屏幕上的这一层永远没有句柄。
  //
  // The report suppressed while the layer was in the background, replayed once it
  // comes forward: the base hands the handle over at mount only, and a swap just
  // flips visibility without remounting, so without the replay the layer now on
  // screen would have no handle at all.
  const suppressed = useRef<{
    handle?: ViewerHandle | null;
    modes?: readonly ViewerViewModeOption[];
  }>({});
  // 回调用 ref 取最新值，`adapters` 因此不随层级变化重建。它一变，底座就把渲染
  // 管线整个重启——连屏幕上那一层也会退回加载态，双层过渡等于白做。层级切换时
  // 只有 `tab.source` 该变，回调只是「往哪儿送」。
  //
  // Callbacks are read through refs so `adapters` survives a level change. A new
  // adapters array restarts the base's whole pipeline — even the layer on screen would
  // fall back to a loading state, wasting the two-layer transition. Only `tab.source`
  // should change on a level switch; the callbacks just say where reports go.
  const callbacks = useRef({
    onHandleChange,
    onViewModesChange,
    onLinkClick,
    resolveImage: imageResolver ? resolveImage : undefined,
  });
  callbacks.current = {
    onHandleChange,
    onViewModesChange,
    onLinkClick,
    resolveImage: imageResolver ? resolveImage : undefined,
  };
  const adapters = useMemo(
    () =>
      createFilePreviewAdapters({
        onHandleChange: (handle) => {
          suppressed.current.handle = handle;
          if (reportsRef.current) callbacks.current.onHandleChange(handle);
        },
        onViewModesChange: (modes) => {
          suppressed.current.modes = modes;
          if (reportsRef.current) callbacks.current.onViewModesChange(modes);
        },
        onLinkClick: (event, link) =>
          callbacks.current.onLinkClick?.(event, link),
        resolveImage: callbacks.current.resolveImage
          ? (src: string) => callbacks.current.resolveImage!(src)
          : undefined,
      }),
    [],
  );
  // useEffect 必要性：补发要在 `reports` 变 true 的那次提交之后进行，此时句柄
  // 早已存在；渲染期间调用会更新父组件的 state，触发渲染阶段的状态更新警告。
  // 没有需要清理的资源，依赖变化只会让补发发生在最新一次上报上。
  //
  // The replay must run after the commit that flips `reports`; calling it during
  // render would update the parent's state mid-render. Nothing to clean up.
  useEffect(() => {
    if (!reports) return;
    if ('handle' in suppressed.current)
      onHandleChange(suppressed.current.handle ?? null);
    if (suppressed.current.modes) onViewModesChange(suppressed.current.modes);
  }, [reports, onHandleChange, onViewModesChange]);

  // 底座把 `slots` 里的函数当依赖，换新函数会重建渲染器。而这三个来源在宿主
  // 那边每次渲染都是新值：`tabText` 是新建对象、
  // `tab.slots` 写在 tabs 数组里同样是字面量。若直接进依赖数组，`slots` 就每次
  // 都变，表现为点击任意按钮后整个正文「闪一下」——渲染器被卸载重建。
  //
  // 全部用 ref 持有最新值，让 `slots` 的依赖数组为空：它只在本次挂载时创建
  // 一次，之后正文在复制、切换视图等操作下保持挂载。切页签时 `key` 变化会让
  // 整个组件重建，ref 随之更新，因此不会读到上一个页签的值。
  //
  // The base treats the functions inside `slots` as dependencies and remounts the
  // renderer when they change. All three sources are fresh on every host render:
  // `tabText` is a new object and `tab.slots` a
  // literal inside the tabs array. Putting them in the dependency array made
  // `slots` change on every render — which showed up as the whole body "flashing"
  // after any click, the renderer being torn down and rebuilt.
  //
  // A ref holds the latest values so `slots` has an empty dependency array: it is
  // built once per mount and the body stays mounted across copy, view switch and
  // friends. Switching tabs changes `key`, which remounts everything and refreshes
  // the ref, so it never reads the previous tab's values.
  const latest = useRef({ text, tabSlots: tab.slots, onLoadingChange });
  latest.current = { text, tabSlots: tab.slots, onLoadingChange };

  const slotPresence = [
    Boolean(tab.slots?.confirmFallback),
    Boolean(tab.slots?.downloadFallback),
  ].join();
  const slots = useMemo<FileViewerCoreProps['slots']>(() => {
    // 分支在调用时才决定：`slots` 只建一次（空依赖），而宿主可能在挂载后换页签
    // 或增删插槽。创建时定死「用宿主的还是内置的」，切到没有该插槽的页签后会继续
    // 渲染上一页的节点，表现为空白。
    //
    // Decide the branch when called: `slots` is built once while the host may switch
    // tabs or add/remove slots afterwards. Fixing it at creation time would keep
    // rendering the previous tab's node after switching, showing up as a blank body.
    // 加载态起止经 reporter 上报：外壳 `ready` 之后渲染器的遮罩还会再停一两帧。
    // Loading start and end go through the reporter: the renderer's overlay lingers.
    const loadingSlot = () => (
      <FilePreviewLoadingReporter
        onLoadingChange={(loading) => latest.current.onLoadingChange?.(loading)}
      >
        {latest.current.tabSlots?.loadingFallback ?? (
          <LoadingState text={latest.current.text} />
        )}
      </FilePreviewLoadingReporter>
    );
    const errorSlot: NonNullable<FileViewerCoreProps['slots']>['error'] = ({
      retry,
    }) => {
      const own = latest.current.tabSlots?.errorFallback;
      if (own) return <>{own}</>;
      return <LoadFailedState text={latest.current.text} onRetry={retry} />;
    };
    // 只传宿主提供的插槽；优先级由底座决定。
    // Only supply host slots; the base owns their precedence.
    const own = tab.slots;
    const hasConfirm = Boolean(own?.confirmFallback);
    const hasHostDownload = Boolean(own?.downloadFallback);
    // 传稳定 wrapper，内部从 `latest.current` 读当前组件并转发 props。直接传宿主
    // 组件会把它冻在这一帧：同一 key 下宿主换了组件，memo 不重建，界面停在旧组件。
    //
    // A stable wrapper reads the current component from `latest.current` and forwards
    // props; passing the host's component directly would freeze it at this frame.
    const confirmSlot: NonNullable<FileViewerCoreProps['slots']>['confirm'] = (
      props,
    ) => {
      const Host = latest.current.tabSlots?.confirmFallback;
      return Host ? <Host {...props} /> : null;
    };
    const downloadSlot: NonNullable<
      FileViewerCoreProps['slots']
    >['downloadFallback'] = (props) => {
      const tabSlots = latest.current.tabSlots;
      const Host = tabSlots?.downloadFallback;
      if (Host) return <Host {...props} />;
      return null;
    };
    return {
      loading: loadingSlot,
      error: errorSlot,
      ...(hasConfirm ? { confirm: confirmSlot } : {}),
      ...(hasHostDownload ? { downloadFallback: downloadSlot } : {}),
    };
    // 依赖用「哪些插槽存在」而非对象身份：宿主每次渲染都会重建 `slots`，用对象
    // 会让 memo 每帧重建、底座重挂渲染器（表现为点击后正文闪一下）。
    //
    // Key on which slots exist, not object identity: the host rebuilds `slots` every
    // render, and keying on it would remount the base renderer on every click.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab.key, slotPresence]);

  if (!source) {
    // 连可预览的来源都没有，底座无从判断格式，只能由组件兜底。
    return tab.slots?.unsupportedFallback ?? <UnsupportedState text={text} />;
  }

  return (
    <FileViewerCore
      key={tab.key}
      adapters={adapters}
      className={
        viewMode === 'source'
          ? 'ov-file-preview__viewer ov-file-preview__viewer--source'
          : 'ov-file-preview__viewer'
      }
      locale={locale}
      resources={resources}
      slots={slots}
      source={source}
      toolbar={false}
      capabilities={{ download: false }}
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
      onStateChange={onStateChange}
      viewMode={viewMode}
      onViewModeChange={(mode) => {
        if (mode === 'preview' || mode === 'source') onViewModeChange(mode);
      }}
    />
  );
}
