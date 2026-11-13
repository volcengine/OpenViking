import type { Locale } from '../../shared/locale.js';
import { FileViewerCore } from '@ark-design/ui/file-viewer/core';
import type {
  FileViewerCoreProps,
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import { useCallback, useMemo, useRef } from 'react';

import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { createFilePreviewAdapters } from './file-preview-handle-adapters.js';
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
  const adapters = useMemo(
    () =>
      createFilePreviewAdapters({
        onHandleChange,
        onViewModesChange,
        onLinkClick,
        resolveImage: imageResolver ? resolveImage : undefined,
      }),
    [
      onHandleChange,
      onViewModesChange,
      onLinkClick,
      resolveImage,
      imageResolver,
    ],
  );

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
  const latest = useRef({ text, tabSlots: tab.slots });
  latest.current = { text, tabSlots: tab.slots };

  const slotPresence = [
    Boolean(tab.slots?.confirmFallback),
    Boolean(tab.slots?.downloadFallback),
  ].join();
  const slots = useMemo<FileViewerCoreProps['slots']>(() => {
    // 分支在调用时才决定，不在创建时决定：`slots` 只建一次（空依赖），而
    // 宿主可能在挂载后换页签或增删插槽。创建时就把「用宿主的还是内置的」定死，
    // 切到没有该插槽的页签后会继续渲染上一页的节点，表现为空白。
    //
    // Decide the branch when called, not when created: `slots` is built once (empty
    // dependencies) while the host may switch tabs or add/remove slots afterwards.
    // Fixing "host's or built-in" at creation time would keep rendering the previous
    // tab's node after switching, showing up as a blank body.
    const loadingSlot = () => {
      const own = latest.current.tabSlots?.loadingFallback;
      return own ? <>{own}</> : <LoadingState text={latest.current.text} />;
    };
    const errorSlot: NonNullable<FileViewerCoreProps['slots']>['error'] = ({
      retry,
    }) => {
      const own = latest.current.tabSlots?.errorFallback;
      return own ? (
        <>{own}</>
      ) : (
        <LoadFailedState text={latest.current.text} onRetry={retry} />
      );
    };
    // 只传宿主提供的插槽。省略下载插槽时保留底座的默认下载兜底；
    // downloadFallback、unsupportedFallback、默认界面的优先级由底座决定。
    // Only supply host slots: omission retains FileViewer's default download UI.
    // The base owns precedence between downloadFallback, unsupportedFallback and its default.
    const own = tab.slots;
    const hasConfirm = Boolean(own?.confirmFallback);
    const hasHostDownload = Boolean(own?.downloadFallback);
    // 传的是稳定 wrapper，内部从 `latest.current` 读当前组件并转发 props。
    // 直接把宿主组件本身传进去会把它冻在这一帧：同一个 key 下宿主换了组件
    // （新的文案或回调闭包），memo 不重建，界面仍停在旧组件上。
    //
    // What goes in is a stable wrapper that reads the current component from
    // `latest.current` and forwards props. Passing the host's component directly
    // freezes it at this frame: a new component under the same key (fresh copy or
    // callback closure) would not take effect while the memo holds.
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
    // 依赖用「哪些插槽存在」而不是对象身份：宿主每次渲染都会重建 `slots`，
    // 用对象会让 memo 每帧重建、底座重挂渲染器（表现为点击后正文闪一下）。
    //
    // Key on which slots exist rather than on object identity: the host rebuilds
    // `slots` every render, and keying on it would rebuild this memo each frame and
    // remount the base renderer — the body "flashing" after any click.
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
      viewMode={viewMode}
      onViewModeChange={(mode) => {
        if (mode === 'preview' || mode === 'source') onViewModeChange(mode);
      }}
    />
  );
}
