import type {
  Adapter,
  FileViewerCoreProps,
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';

import { FILE_PREVIEW_ADAPTERS } from './file-preview-adapters.js';

export interface FilePreviewAdapterOptions {
  /** 底座交出或收回命令式句柄时调用。 */
  onHandleChange: (handle: ViewerHandle | null) => void;
  /** 命中的 Adapter 加载完成时，上报它支持的视图模式。 */
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
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
}: FilePreviewAdapterOptions): FileViewerCoreProps['adapters'] {
  return FILE_PREVIEW_ADAPTERS.map((adapter) => {
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
            const register = props.registerHandle;
            const proxied = {
              ...props,
              registerHandle: (handle: ViewerHandle | null) => {
                onHandleChange(handle);
                register?.(handle);
              },
            };
            return <Component {...proxied} />;
          }
          HandleCapture.displayName = 'FilePreviewHandleCapture';
          return { Component: HandleCapture };
        },
      };
    };
    return wrap;
  });
}
