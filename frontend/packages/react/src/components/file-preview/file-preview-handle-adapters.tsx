import type {
  Adapter,
  FileViewerCoreProps,
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import { useCallback, useRef } from 'react';

import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { createFilePreviewAdapterList } from './file-preview-adapters.js';

export interface FilePreviewAdapterOptions {
  /** 底座交出或收回命令式句柄时调用。 */
  onHandleChange: (handle: ViewerHandle | null) => void;
  /** 命中的 Adapter 加载完成时，上报它支持的视图模式。 */
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  /** Markdown 正文里的链接被点击时调用；传入即由宿主接管导航。 */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
  resolveImage?: (src: string) => Promise<string>;
}

/**
 * 给每个 Adapter 套一层：接住底座交出来的命令式句柄，并上报命中格式支持
 * 的视图模式。
 *
 * 底座没有 forwardRef，句柄是通过渲染组件的 `registerHandle` 属性交出的；
 * `load()` 只会对命中的那个 Adapter 调用一次，因此在这里上报视图模式就等价
 * 于「当前文件支持哪些视图」，组件据此决定是否显示预览/源码切换。
 *
 * Wraps every adapter to capture the imperative handle and to report the view
 * modes the matched format supports. The base has no forwardRef: the handle
 * arrives through the renderer's `registerHandle` prop. `load()` runs once for
 * the matched adapter only, so reporting there is equivalent to "which views
 * this file supports", and the component uses it to decide whether to render the
 * preview/source switch.
 */
export function createFilePreviewAdapters({
  onHandleChange,
  onViewModesChange,
  onLinkClick,
  resolveImage,
}: FilePreviewAdapterOptions): FileViewerCoreProps['adapters'] {
  return createFilePreviewAdapterList({ onLinkClick, resolveImage }).map(
    (adapter) => {
      const wrap: Adapter = (context) => {
        const descriptor = adapter(context);
        return {
          ...descriptor,
          load: async (loadContext) => {
            const { Component } = await descriptor.load(loadContext);
            onViewModesChange(descriptor.supportedViewModes ?? []);
            function HandleCapture(props: {
              registerHandle?: (handle: ViewerHandle | null) => void;
            }) {
              // `proxy` 必须完全稳定：底座把 `registerHandle` 当依赖，每次渲染
              // 换一个新函数会让它重新 setState，我们的回调再 setState，形成
              // 无限循环。底座传下来的 `registerHandle` 也可能每次渲染都变，
              // 因此用 ref 持有最新的那个，让 `proxy` 的依赖数组为空。
              //
              // `proxy` must be fully stable: the base treats `registerHandle` as a
              // dependency, and a fresh function every render makes it setState
              // again, which our callback answers with another setState — an endless
              // loop. The `registerHandle` the base passes in can change per render
              // too, so a ref holds the latest one and `proxy` keeps an empty dep list.
              const latest = useRef(props.registerHandle);
              latest.current = props.registerHandle;
              const proxy = useCallback((handle: ViewerHandle | null) => {
                onHandleChange(handle);
                latest.current?.(handle);
              }, []);
              return <Component {...props} registerHandle={proxy} />;
            }
            HandleCapture.displayName = 'FilePreviewHandleCapture';
            return { Component: HandleCapture };
          },
        };
      };
      return wrap;
    },
  );
}
